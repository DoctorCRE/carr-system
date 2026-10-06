/**
 * Recent change receipts for the Deal Room board.
 *
 * Pure model: no DOM, no network, no storage, no timers. app.js feeds this the
 * events its existing change poll already returns and renders the strings this
 * module returns, so the panel adds no transport and no second read path.
 *
 * What this list is, exactly:
 *   - the newest RECEIPT_LIMIT changes THIS SESSION HAS SEEN on the feed. Not
 *     "changes made since you opened the page", and not a window over the
 *     event log: the tail of what was already recorded is in it too, once the
 *     cursor reaches the present (see createFeedProgress).
 *   - session memory only. It lives in the page; nothing is written to browser
 *     storage and no new persistence surface exists. A reload starts it empty.
 *   - the durable record is unchanged: every one of these changes stays in the
 *     deal's own Change history, which is where the full lineage lives.
 *   - Undo eligibility shown here is a HINT drawn from the events this session
 *     has seen. `revert-deal-field` on the server is the gate: it refuses
 *     anything that is no longer the latest change to that exact deal+field
 *     (including a partner's newer change), and that refusal stays visible on
 *     the row rather than being swallowed.
 *
 * Values are shown as the feed recorded them. Slug-shaped strings are
 * humanized in shape only (`due_diligence` -> "Due diligence"); no value is
 * remapped onto a different label, because the display vocabulary lives in
 * live-client.js and inventing a second copy here would let the two drift.
 */

import { escapeHtml as escapeText } from './esc.js';

/**
 * The fields `revert-deal-field` will act on. This is the client-side home for
 * that list — app.js imports it back rather than keeping its own copy — and it
 * mirrors DEAL_ROOM_FIELDS in mcp-server/src/tools.js, which is the authority.
 */
export const REVERTIBLE_FIELDS = Object.freeze(['phase', 'owner', 'attention', 'next_date', 'operating_state']);

/** How many receipts the session keeps. Oldest fall off first. */
export const RECEIPT_LIMIT = 25;

/** Longest value string rendered inline; the deal record holds the full text. */
const VALUE_MAX = 140;

const FIELD_LABEL = Object.freeze({
  phase: 'Phase',
  owner: 'Owner',
  attention: 'Attention flag',
  next_date: 'Next date',
  next_step: 'Next step',
  operating_state: 'Active work',
  note: 'Note',
  market_agent: 'Market agent',
});

const PARKING_REASON_LABEL = Object.freeze({
  prospect_never_active: 'Prospect never became active work',
  client_paused: 'Client paused activity',
  other: 'Not active right now',
});

// Fallback wording, used ONLY when a decline arrives without a hint of its own
// (key_reuse travels bare). Anything the server says about this request is
// shown instead of these, verbatim — see declineOutcome.
const REFUSAL_MESSAGE = Object.freeze({
  newer_change_exists: 'A newer change to this field came first, so the server refused this undo. Open the deal and review the current value.',
  event_not_revertible: 'The server does not undo this kind of change.',
  // A safety stop, not a decline of the value: the same key already carried a
  // different request. Terminal on purpose — re-sending is what it prevents.
  key_reuse: 'This undo was already sent under the same safety key for a different request. Open the deal and check its current value.',
});

// Codes that mean the SERVER broke, not that it declined. An unhandled throw
// inside a verb comes back on the same isError channel as a real refusal
// (mcp-server/src/mcp.js), and its own hint says so in as many words. Treating
// it as a refusal would badge the row "Refused", print a paragraph of server
// diagnostics at a partner, and close the row to any retry — while the write
// may in fact have landed. It is an unknown, and it stays retryable.
const SERVER_FAULT_CODES = new Set(['unhandled_verb_failure', 'internal_error']);

const UNKNOWN_MESSAGE = Object.freeze({
  fault: 'Undo could not be confirmed — the server hit an error before it answered. Open the deal to check before trying again.',
  silent: 'Undo could not be confirmed — no answer from the server. Open the deal to check before trying again.',
});

const MONTHS = Object.freeze(['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']);

const SLUG = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$/;

/** Escape for HTML text and attribute contexts. app.js uses this as its `esc`.
 * One implementation, in esc.js; re-exported here under its historical name. */
export { escapeText };

/** Why a work record is parked. Single home; app.js imports this. */
export function parkingReasonLabel(reason) {
  return PARKING_REASON_LABEL[reason] || 'Parked';
}

function humanizeSlug(text) {
  const value = String(text);
  if (!SLUG.test(value)) return value;
  const spaced = value.replace(/_/g, ' ');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function truncate(text) {
  const value = String(text);
  return value.length > VALUE_MAX ? `${value.slice(0, VALUE_MAX - 1)}…` : value;
}

export function fieldLabel(field) {
  if (!field) return null;
  return FIELD_LABEL[field] || humanizeSlug(field);
}

export function verbLabel(verb) {
  if (!verb) return 'Change recorded';
  const spaced = String(verb).replace(/-/g, ' ');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * One side of a change, as a person can read it. Never a score, never a guess:
 * an absent value reads "(empty)" and an unknown shape falls back to its JSON.
 */
export function readableValue(field, value, options = {}) {
  const actorLabel = options.actorLabel || ((slug) => slug);
  if (value === null || value === undefined || value === '') return '(empty)';
  if (field === 'operating_state' && typeof value === 'object') {
    return value.state === 'parked' ? `Parked — ${parkingReasonLabel(value.reason)}` : 'Active work';
  }
  if (typeof value === 'boolean') {
    if (field === 'attention') return value ? 'Flagged' : 'Not flagged';
    return value ? 'Yes' : 'No';
  }
  if (typeof value === 'number') return String(value);
  if (field === 'owner' && typeof value === 'string') return actorLabel(value) || value;
  if (typeof value === 'string') return truncate(humanizeSlug(value));
  return truncate(JSON.stringify(value));
}

/**
 * Turn one polled event into a receipt, or return null when it is not this
 * board's business. Everything on the receipt traces to the event: no deal,
 * actor, field, or value is inferred.
 */
export function normalizeChangeEvent(event, options = {}) {
  if (!event || typeof event !== 'object') return null;
  const eventId = typeof event.id === 'string' ? event.id : '';
  const dealId = typeof event.subject_id === 'string' ? event.subject_id : '';
  if (!eventId || !dealId) return null;
  if (event.subject_type && event.subject_type !== 'deal') return null;
  const dealName = options.dealName ? options.dealName(dealId) : null;
  // The board is the scope. A record this session cannot name is left out
  // rather than narrated as "unknown deal".
  if (!dealName) return null;

  const actorLabel = options.actorLabel || ((slug) => slug);
  const field = typeof event.field === 'string' && event.field ? event.field : null;
  const kind = field === 'note' ? 'note' : field ? 'field' : 'activity';
  const actor = typeof event.actor === 'string' && event.actor ? event.actor : null;
  const receipt = {
    event_id: eventId,
    deal_id: dealId,
    deal_name: String(dealName),
    actor,
    actor_label: actor ? (actorLabel(actor) || actor) : 'Unattributed',
    recorded_at: typeof event.recorded_at === 'string' ? event.recorded_at : null,
    verb: typeof event.verb === 'string' ? event.verb : null,
    field,
    kind,
    // A note receipt says a note was added and links to the record. The note
    // text itself is not copied here; it belongs in the deal's thread.
    action: kind === 'note' ? 'Note added' : field ? fieldLabel(field) : verbLabel(event.verb),
    before: null,
    after: null,
    revertible_field: Boolean(field) && REVERTIBLE_FIELDS.includes(field),
  };
  if (kind === 'field' && ('old_value' in event || 'new_value' in event)) {
    receipt.before = readableValue(field, event.old_value, { actorLabel });
    receipt.after = readableValue(field, event.new_value, { actorLabel });
  }
  return receipt;
}

const TIMESTAMP = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(?:\.(\d+))?\s*([Zz]|[+-]\d{2}(?::?\d{2})?)?$/;

/**
 * Reduce a recorded_at to a comparable instant, keeping the precision the
 * server sent.
 *
 * Two things make the raw string useless for ordering: the same instant can be
 * written at different UTC offsets, and Postgres records microseconds while
 * Date.parse stops at milliseconds. So the offset is normalized and parsed
 * (which settles the instant), and the sub-millisecond remainder is carried
 * alongside it (which settles precision). Nothing is ever ordered by comparing
 * timestamp text.
 *
 * @returns {{ms:number, sub:number}|null} sub is nanoseconds past the whole
 *   millisecond; null means the value cannot be placed in time at all.
 */
export function receiptInstant(recordedAt) {
  const text = String(recordedAt ?? '').trim();
  if (!text) return null;
  const parsed = TIMESTAMP.exec(text);
  if (!parsed) {
    const loose = Date.parse(text);
    return Number.isFinite(loose) ? { ms: loose, sub: 0 } : null;
  }
  const [, date, clock, fraction = '', zone = ''] = parsed;
  const time = clock.length === 5 ? `${clock}:00` : clock;
  // Right-pad to nanoseconds so ".5", ".123456" and ".123456789" are one scale;
  // anything finer than a nanosecond is beyond what the record layer stores.
  const nanos = Number(`${fraction}000000000`.slice(0, 9));
  let offset = '';
  if (zone) {
    if (zone.length === 1) offset = 'Z';
    else {
      const digits = zone.slice(1).replace(':', '');
      offset = `${zone[0]}${digits.slice(0, 2)}:${digits.slice(2) || '00'}`;
    }
  }
  const ms = Date.parse(`${date}T${time}.${String(Math.floor(nanos / 1e6)).padStart(3, '0')}${offset}`);
  return Number.isFinite(ms) ? { ms, sub: nanos % 1e6 } : null;
}

/**
 * Newest first, on the same (recorded_at, id) key the server orders and
 * paginates by: instant, then the precision below a millisecond, then the
 * event id — the server's own tie-break, reversed. A timestamp that cannot be
 * placed sorts last rather than being guessed at.
 */
export function compareReceipts(a, b) {
  const at = receiptInstant(a?.recorded_at);
  const bt = receiptInstant(b?.recorded_at);
  if (at && bt) {
    if (at.ms !== bt.ms) return bt.ms - at.ms;
    if (at.sub !== bt.sub) return bt.sub - at.sub;
  } else if (at || bt) {
    return at ? -1 : 1;
  }
  const ai = String(a?.event_id || '');
  const bi = String(b?.event_id || '');
  return ai < bi ? 1 : ai > bi ? -1 : 0;
}

/**
 * Fold a polled batch into the session list. Repeated batches and out-of-order
 * batches are both ordinary here: the event id is the identity, and the whole
 * list is re-sorted, so a replayed cursor adds nothing and a late arrival
 * lands in its right place.
 */
export function ingestChangeEvents(receipts, events, options = {}) {
  const byId = new Map();
  for (const existing of Array.isArray(receipts) ? receipts : []) {
    if (existing && typeof existing.event_id === 'string') byId.set(existing.event_id, existing);
  }
  for (const event of Array.isArray(events) ? events : []) {
    const receipt = normalizeChangeEvent(event, options);
    if (!receipt || byId.has(receipt.event_id)) continue;
    byId.set(receipt.event_id, receipt);
  }
  return [...byId.values()].sort(compareReceipts).slice(0, RECEIPT_LIMIT);
}

/**
 * How far the session's change cursor has got.
 *
 * The changes feed is a cursor over the WHOLE deal event log, oldest first, one
 * server page at a time. A session that opens with a null cursor therefore
 * receives the OLDEST page first, and keeps receiving pages until it reaches
 * the present. Folding those pages straight onto the surface would title
 * ancient history "Recent changes" and offer Undo on a months-old change that
 * only looks latest because nothing newer has arrived yet.
 *
 * So the list stays closed until the cursor is current. The page size is not
 * published by the server and is not assumed here — it is LEARNED: the largest
 * page yet seen is the page size, and the first page that comes back short of
 * it (or empty) is the one that reached the present. No clock is consulted; a
 * browser's idea of "now" has no authority over what the server has recorded.
 * Once current, it stays current — later pages are increments.
 *
 * Cost of learning rather than assuming: when the whole log fits in one page,
 * that page is indistinguishable from a full one, so the list opens on the
 * following poll instead of the first.
 */
export function createFeedProgress() {
  return { page_size: 0, caught_up: false, pages: 0 };
}

export function observeChangeBatch(progress, batch) {
  const current = progress || createFeedProgress();
  const size = Array.isArray(batch) ? batch.length : 0;
  const pageSize = Math.max(current.page_size || 0, size);
  return {
    page_size: pageSize,
    caught_up: Boolean(current.caught_up) || size === 0 || size < pageSize,
    pages: (current.pages || 0) + 1,
  };
}

/**
 * A short line for a status region, naming only what ARRIVED since the last
 * render. The list itself is not a live region: replacing 25 rows would make a
 * screen reader read all 25 back on every single change.
 *
 * `previousIds` of null means the list has just opened — the first exposure
 * announces nothing, because none of it is news.
 */
export function receiptsAnnouncement(previousIds, views) {
  if (!Array.isArray(previousIds)) return '';
  const known = new Set(previousIds);
  const fresh = (Array.isArray(views) ? views : []).filter((view) => !known.has(view.event_id));
  if (!fresh.length) return '';
  if (fresh.length === 1) return `1 new change · ${fresh[0].deal_name} · ${fresh[0].action}`;
  return `${fresh.length} new changes`;
}

/** Clock time for today's changes, dated for anything older. Never "3 minutes ago", which goes stale in a list that is only re-rendered when it changes. */
export function receiptTimeLabel(recordedAt, now = Date.now()) {
  const instant = receiptInstant(recordedAt);
  if (!instant) return 'time not recorded';
  const at = new Date(instant.ms);
  const clock = `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`;
  if (new Date(now).toDateString() === at.toDateString()) return clock;
  return `${MONTHS[at.getMonth()]} ${at.getDate()} · ${clock}`;
}

/** Undo bookkeeping, keyed by event id. Plain data so it stays comparable. */
export function createUndoState() {
  return {};
}

/**
 * Claim the single in-flight undo for one event.
 *
 * `started: false` is the double-click guard: a second click while a request
 * is open, or after the server already answered, issues no second write. The
 * idempotency key is minted once per event and REUSED on a later deliberate
 * retry, so one logical undo carries one key however many times it is sent.
 */
export function beginUndo(state, eventId, newKey) {
  const undo = state || {};
  const current = undo[eventId] || null;
  if (current && ['pending', 'succeeded', 'refused'].includes(current.status)) {
    return { state: undo, started: false, request: null, reason: current.status };
  }
  const idempotencyKey = current?.idempotency_key || newKey();
  return {
    state: { ...undo, [eventId]: { status: 'pending', idempotency_key: idempotencyKey, message: null, code: null } },
    started: true,
    request: { event_id: eventId, idempotency_key: idempotencyKey },
    reason: null,
  };
}

/** Record what the server actually said. Nothing else moves the status. */
export function settleUndo(state, eventId, outcome) {
  const undo = state || {};
  const current = undo[eventId];
  if (!current) return undo;
  return {
    ...undo,
    [eventId]: {
      ...current,
      status: outcome?.status || 'unknown',
      message: outcome?.message || null,
      code: outcome?.code || null,
    },
  };
}

function declineOutcome(code, hint) {
  if (code && SERVER_FAULT_CODES.has(code)) {
    // Deliberately NOT the server's hint: that text is written for whoever
    // maintains the verb, and it is not a statement about this deal.
    return { status: 'unknown', code, message: UNKNOWN_MESSAGE.fault };
  }
  return {
    status: 'refused',
    code: code || null,
    // The server's own hint FIRST, verbatim. It is the only text that knows
    // why this particular request was declined; the table below is a fallback
    // for the codes that travel without one (key_reuse does), never a rewrite
    // of something the server took the trouble to say.
    message: hint || (code ? REFUSAL_MESSAGE[code] || `The server refused this undo: ${humanizeSlug(code)}.` : 'The server refused this undo.'),
  };
}

/**
 * Classify one revert-deal-field answer.
 *
 * Three outcomes, and the difference between the last two is the whole point:
 *
 *   succeeded — the server said `ok: true`. Nothing else counts.
 *   refused   — the server DECLINED the request: a newer change won, the field
 *               is not revertible, the safety key was reused. Terminal and
 *               visible, because re-sending would not change the answer.
 *   unknown   — we do not know whether the write landed: no answer, no result,
 *               or the server's own exception on the way through. The row is
 *               not marked undone, no deal value is touched, nothing is retried
 *               automatically, and the control stays available so a person can
 *               deliberately re-send under the SAME idempotency key.
 */
export function classifyUndoOutcome({ response = null, error = null } = {}) {
  if (error) {
    const code = error?.payload?.error || null;
    if (code) return declineOutcome(code, error?.payload?.hint);
    return { status: 'unknown', code: null, message: UNKNOWN_MESSAGE.silent };
  }
  if (response && response.ok === true) {
    return { status: 'succeeded', code: null, message: null };
  }
  if (response && response.ok === false) {
    return declineOutcome(response.error || null, response.hint);
  }
  return { status: 'unknown', code: null, message: UNKNOWN_MESSAGE.silent };
}

/**
 * Run one undo end to end against an injected `revert` call — in the app that
 * is client.revertDealField, the verb the Deal Room already uses.
 *
 * The claim is published through `setState` BEFORE the request is awaited, so
 * a second click during the round trip sees the pending entry and returns
 * without sending anything. The status only moves on the server's own answer.
 *
 * @param {Object} args
 * @param {string} args.eventId
 * @param {() => Object} args.getState
 * @param {(state:Object) => void} args.setState
 * @param {() => string} args.newKey
 * @param {(request:{event_id:string, idempotency_key:string}) => Promise<any>} args.revert
 */
export async function performUndo({ eventId, getState, setState, newKey, revert }) {
  const claim = beginUndo(getState(), eventId, newKey);
  setState(claim.state);
  if (!claim.started) return { started: false, outcome: null, reason: claim.reason };
  let response = null;
  let error = null;
  try {
    response = await revert({ event_id: claim.request.event_id, idempotency_key: claim.request.idempotency_key });
  } catch (caught) {
    error = caught;
  }
  const outcome = classifyUndoOutcome({ response, error });
  setState(settleUndo(getState(), eventId, outcome));
  return { started: true, outcome, reason: null, idempotency_key: claim.request.idempotency_key };
}

const NO_UNDO_BADGE = Object.freeze({
  undone: 'Undone',
  pending: 'Undoing…',
  refused: 'Refused',
  superseded: 'Superseded',
  unconfirmed: 'Not confirmed',
});

/**
 * Derive what each row should say right now. Supersession is recomputed from
 * the whole list every time, so a partner's newer change to the same
 * deal+field retires the older row's Undo exactly as the server would.
 */
export function receiptViews(receipts, options = {}) {
  const list = (Array.isArray(receipts) ? [...receipts] : []).sort(compareReceipts);
  const undo = options.undo || {};
  const selfActor = options.selfActor || null;
  const now = options.now ?? Date.now();

  const latestForField = new Map();
  for (const receipt of list) {
    if (!receipt.revertible_field) continue;
    const key = `${receipt.deal_id}|${receipt.field}`;
    if (!latestForField.has(key)) latestForField.set(key, receipt.event_id);
  }

  return list.map((receipt) => {
    const entry = undo[receipt.event_id] || null;
    const status = entry?.status || 'idle';
    const own = Boolean(selfActor) && receipt.actor === selfActor;
    const superseded = receipt.revertible_field
      && latestForField.get(`${receipt.deal_id}|${receipt.field}`) !== receipt.event_id;

    let undoStatus;
    if (status === 'succeeded') undoStatus = 'undone';
    else if (status === 'pending') undoStatus = 'pending';
    else if (status === 'refused') undoStatus = 'refused';
    else if (!receipt.revertible_field) undoStatus = 'unsupported';
    else if (!own) undoStatus = 'partner';
    else if (superseded) undoStatus = 'superseded';
    else if (status === 'unknown') undoStatus = 'unconfirmed';
    else undoStatus = 'available';

    return {
      event_id: receipt.event_id,
      deal_id: receipt.deal_id,
      deal_name: receipt.deal_name,
      actor: receipt.actor,
      actor_label: receipt.actor_label,
      recorded_at: receipt.recorded_at,
      time_label: receiptTimeLabel(receipt.recorded_at, now),
      kind: receipt.kind,
      field: receipt.field,
      action: receipt.action,
      before: receipt.before,
      after: receipt.after,
      own,
      superseded,
      undo_status: undoStatus,
      // Only "available" and "unconfirmed" offer the control. "unconfirmed"
      // keeps the same idempotency key, so a deliberate second attempt cannot
      // become a second write.
      can_undo: undoStatus === 'available' || undoStatus === 'unconfirmed',
      badge: NO_UNDO_BADGE[undoStatus] || null,
      message: entry?.message || null,
      message_tone: status === 'refused' ? 'refused' : status === 'unknown' ? 'unknown' : null,
    };
  });
}

/**
 * What the rendered list depends on. app.js re-renders only when this changes,
 * which keeps the aria-live log quiet between real changes and keeps focus on
 * an Undo button the partner is still using.
 */
export function receiptsSignature(views) {
  return (Array.isArray(views) ? views : [])
    .map((view) => `${view.event_id}:${view.undo_status}:${view.superseded ? 1 : 0}:${view.message ? 1 : 0}`)
    .join('|');
}

/** One row. Every value from the feed passes through escapeText on the way in. */
export function receiptRowHtml(view) {
  const values = view.before !== null || view.after !== null
    ? `<span class="receipt-values"><span class="receipt-before">${escapeText(view.before)}</span><span class="receipt-arrow" aria-hidden="true">→</span><span class="sr-only">changed to</span><span class="receipt-after">${escapeText(view.after)}</span></span>`
    : '';
  const badge = view.badge
    ? `<span class="receipt-badge ${escapeText(view.undo_status)}">${escapeText(view.badge)}</span>`
    : '';
  const undoControl = view.can_undo
    ? `<button type="button" class="receipt-undo" data-undo="${escapeText(view.event_id)}">${view.undo_status === 'unconfirmed' ? 'Try undo again' : 'Undo'}</button>`
    : view.undo_status === 'pending'
      ? '<button type="button" class="receipt-undo" disabled>Undoing…</button>'
      : '';
  const message = view.message
    ? `<p class="receipt-message ${escapeText(view.message_tone || '')}">${escapeText(view.message)}</p>`
    : '';
  // tabindex="-1" keeps the row out of the tab order but available as a focus
  // target: an Undo button that succeeds, is refused, or goes pending is
  // removed or replaced, and the keyboard has to land somewhere sensible.
  return `<li class="receipt${view.superseded ? ' superseded' : ''}" data-receipt="${escapeText(view.event_id)}" tabindex="-1">
    <p class="receipt-line"><button type="button" class="receipt-deal" data-open-deal="${escapeText(view.deal_id)}">${escapeText(view.deal_name)}</button><span class="receipt-action">${escapeText(view.action)}</span>${values}</p>
    <p class="receipt-meta"><span class="receipt-actor">${escapeText(view.actor_label)}</span><span aria-hidden="true">·</span><time class="receipt-time" datetime="${escapeText(view.recorded_at || '')}">${escapeText(view.time_label)}</time>${badge}</p>
    ${undoControl ? `<div class="receipt-actions">${undoControl}</div>` : ''}${message}
  </li>`;
}

export function receiptListHtml(views) {
  return (Array.isArray(views) ? views : []).map(receiptRowHtml).join('');
}
