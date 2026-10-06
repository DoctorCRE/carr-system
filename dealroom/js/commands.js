/**
 * Shared command registry (V5-J101).
 *
 * checkable_done for V5-J101 requires "the same command issued from the UI
 * and from the Doc yields an equivalent receipt"
 * (doctorcre-v5-astra-integration-review /
 * v5-reviewed-implementation-slice-catalog-and-parallel-groups-2026-09-09).
 * The only way to GUARANTEE that structurally, rather than promise it in two
 * places that can drift, is one function both callers run through. A UI form
 * and the Doc composer both call `runCommand`; neither calls
 * client.setNextStep / client.addDealNote directly. Same inputs, same code
 * path, same receipt shape.
 *
 * A command here names and normalizes a write the DealRoomClient (client.js)
 * already implements — it is not a new backend and does not widen authority.
 * When the client does not implement the underlying call, `run` returns an
 * explicit {status:'unavailable', ...} receipt rather than faking success:
 * see capability-seams.js for the same rule applied to reads.
 *
 * TWO DEFECTS fixed here after independent review of PR #1259, both real
 * data-integrity risks, not polish:
 *
 *   1. live-client.js's setNextStep/addDealNote return the RAW server
 *      payload from mcp-server/src/tools.js's add-deal-note / set-next-step
 *      handlers — `{ok:true, deal_id, note_id|next_step_id, created_at, …}`
 *      — never `{status:'ok', event}` (that shape is fixture-client.js's
 *      own). toReceipt() now accepts EITHER shape. Before this fix, a real
 *      live success was reported as `refused`, and because every call minted
 *      a fresh idempotency_key, a user who believed the (false) failure and
 *      retried would send a NEW key — which the server does not dedupe
 *      against the original — producing a genuine second write. runCommand
 *      now accepts an idempotencyKey and returns the one it used, so a
 *      caller-driven retry of the SAME attempt reuses it and lands on the
 *      server's replay path instead.
 *   2. set_next_step defaulted a missing next_date to null. The UI form only
 *      avoided clearing the date because its <input> is pre-filled with the
 *      deal's current value, so "no edit" still resends it; the Doc composer
 *      has no date field and so always sent null, silently wiping the date.
 *      buildDealContext() is the one place both the form and the Doc build
 *      their command context from: it defaults next_date to the deal's
 *      CURRENT value unless the caller explicitly overrides it.
 */

import { uuidv4 } from './uuid.js';

/**
 * @typedef {Object} CommandReceipt
 * @property {'ok'|'conflict'|'refused'|'unavailable'} status
 * @property {string} command
 * @property {string|null} deal
 * @property {string} at ISO-8601
 * @property {string} idempotencyKey the key this attempt used — reuse it on retry
 * @property {Object} detail
 */

/**
 * The one context builder the UI form and the Doc composer both call. `deal`
 * is a BoardDeal (state.deals.get(id)) or undefined/null when nothing is
 * open. `overrides.nextDate`, when present (even null, meaning "clear it"),
 * wins; when ABSENT, next_date defaults to the deal's own current value so a
 * caller that never mentions a date preserves it instead of wiping it.
 * @param {import('./client.js').BoardDeal|null|undefined} deal
 * @param {{nextDate?: string|null}} [overrides]
 */
export function buildDealContext(deal, overrides = {}) {
  const hasExplicitDate = Object.prototype.hasOwnProperty.call(overrides, 'nextDate');
  return {
    dealId: deal?.id ?? null,
    label: deal?.name ?? deal?.id ?? null,
    nextDate: hasExplicitDate ? overrides.nextDate : (deal?.next_date ?? null),
  };
}

function unavailable(command, deal, idempotencyKey, reason) {
  return { status: 'unavailable', command, deal: deal ?? null, at: new Date().toISOString(), idempotencyKey, detail: { reason } };
}

/**
 * True for either success shape a client may return:
 *  - fixture-client.js: {status:'ok', event}
 *  - live-client.js:    {ok:true, deal_id, note_id|next_step_id, created_at, …}
 *    (the raw mcp-server/src/tools.js handler response — write() passes it
 *    straight through with no wrapping)
 */
function isOk(result) {
  return Boolean(result) && (result.status === 'ok' || result.ok === true);
}

/** Normalizes either success shape into one detail.event both callers can render identically. */
function normalizeEvent(command, result) {
  if (result.event) return result.event; // fixture shape already carries one
  if (command === 'add_note') return { id: result.note_id ?? null, deal_id: result.deal_id ?? null, created_at: result.created_at ?? null };
  if (command === 'set_next_step') {
    return {
      id: result.next_step_id ?? null, deal_id: result.deal_id ?? null, created_at: result.created_at ?? null,
      next_action_id: result.next_action_id ?? null, supersedes: result.supersedes ?? null,
    };
  }
  return result;
}

function toReceipt(command, deal, idempotencyKey, result) {
  const at = new Date().toISOString();
  if (result && result.status === 'conflict')
    return { status: 'conflict', command, deal, at, idempotencyKey, detail: { conflict: result.conflict } };
  if (isOk(result))
    return { status: 'ok', command, deal, at, idempotencyKey, detail: { event: normalizeEvent(command, result) } };
  return { status: 'refused', command, deal, at, idempotencyKey, detail: { result: result ?? null } };
}

export const COMMANDS = {
  set_next_step: {
    label: 'Set next step',
    parse(input, context) {
      const text = String(input ?? '').trim();
      if (!text) return { error: 'Say what happens next.' };
      if (!context.dealId) return { error: 'Open a deal first — set next step needs one to act on.' };
      // context.nextDate already carries the caller's intent — explicit
      // override or the deal's current value — via buildDealContext(); this
      // command never re-derives or defaults it a second time.
      return { deal: context.dealId, text, next_date: context.nextDate ?? null };
    },
    async run(client, args, idempotencyKey) {
      if (typeof client?.setNextStep !== 'function')
        return unavailable('set_next_step', args.deal, idempotencyKey, 'V5-J102 typed lifecycle/record-write surface is not deployed yet.');
      const result = await client.setNextStep({
        deal: args.deal, text: args.text, next_date: args.next_date ?? null,
        idempotency_key: idempotencyKey,
      });
      return toReceipt('set_next_step', args.deal, idempotencyKey, result);
    },
  },
  add_note: {
    label: 'Add note',
    parse(input, context) {
      const text = String(input ?? '').trim();
      if (!text) return { error: 'Write the note text.' };
      if (!context.dealId) return { error: 'Open a deal first — a note needs a record to attach to.' };
      return { deal: context.dealId, text };
    },
    async run(client, args, idempotencyKey) {
      if (typeof client?.addDealNote !== 'function')
        return unavailable('add_note', args.deal, idempotencyKey, 'V5-F01 canonical record-home write surface is not deployed yet.');
      const result = await client.addDealNote({
        deal: args.deal, text: args.text, idempotency_key: idempotencyKey,
      });
      return toReceipt('add_note', args.deal, idempotencyKey, result);
    },
  },
};

/**
 * The one entry point every caller — UI form or Doc composer — must use.
 *
 * Pass `idempotencyKey` back in on a RETRY of the exact same logical attempt
 * (same command, same text, same context) so the server's own dedupe
 * (mcp-server/src/tools.js withEnvelope: same idempotency_key -> replay, no
 * second write) applies. Omit it for a new attempt and a fresh one is minted;
 * the returned receipt always carries the key that attempt used.
 * @param {import('./client.js').DealRoomClient|null|undefined} client
 * @param {string} name a COMMANDS key
 * @param {string} input raw command text
 * @param {{dealId?:string|null, nextDate?:string|null}} [context] build with buildDealContext()
 * @param {{idempotencyKey?: string}} [options]
 * @returns {Promise<CommandReceipt>}
 */
export async function runCommand(client, name, input, context = {}, options = {}) {
  const at = new Date().toISOString();
  const idempotencyKey = options.idempotencyKey || uuidv4();
  const command = COMMANDS[name];
  if (!command)
    return { status: 'refused', command: name, deal: context.dealId ?? null, at, idempotencyKey, detail: { reason: `Unknown command "${name}".` } };
  const parsed = command.parse(input, context);
  if (parsed.error)
    return { status: 'refused', command: name, deal: context.dealId ?? null, at, idempotencyKey, detail: { reason: parsed.error } };
  try {
    return await command.run(client, parsed, idempotencyKey);
  } catch (error) {
    return {
      status: 'refused', command: name, deal: parsed.deal ?? context.dealId ?? null, at, idempotencyKey,
      detail: { reason: error?.payload?.hint || error?.message || 'Command failed.' },
    };
  }
}

export function listCommands() {
  return Object.entries(COMMANDS).map(([name, c]) => ({ name, label: c.label }));
}
