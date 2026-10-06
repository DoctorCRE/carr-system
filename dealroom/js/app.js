import { createClient, PHASES, PHICON, ACTOR_LABEL } from './client.js';
import { deploymentIdentity, resolveDealroomBoot } from './boot-mode.js';
import { uuidv4 } from './uuid.js';
import { registerDocPanelSource, refreshDocContext } from './doc-panel.js';
import { runCommand, buildDealContext } from './commands.js';
import {
  REVERTIBLE_FIELDS, escapeText, parkingReasonLabel, ingestChangeEvents,
  receiptViews, receiptListHtml, receiptsSignature, receiptsAnnouncement,
  createFeedProgress, observeChangeBatch, createUndoState, performUndo, fieldLabel,
} from './change-receipts.mjs';
import {
  createBoardSync, batchTouchesBoard, resolveCurrentRow, SYNC_STATES, HEALTH,
} from './board-sync.mjs';
import {
  cellKey, createFieldWriteState, performFieldWrite, unresolvedFieldWrites,
  pendingFieldWrite, fieldWriteMessage, nextCellBase,
} from './field-write-reconciliation.mjs';

const POLL_MS = 1400;
/**
 * How long the board will go without ASKING the record layer again.
 *
 * Not a freshness guarantee and not a claim about the feed. The changes cursor
 * can miss an event whose commit landed out of order with its recorded time, so
 * a board that only re-reads on news can hold a wrong value indefinitely — this
 * is the ceiling on that. It is a bound on how long we go without asking, never
 * a bound on how old a value is: a slow or failed read extends staleness
 * without limit, and the badge says so.
 */
const BOARD_REFRESH_MS = 15000;
const state = {
  client: null, selfActor: null, deals: new Map(), accounts: [],
  // The deal #dealDialog is currently open on, or null. The Doc panel reads
  // this (via registerDocPanelSource below) to know what a typed command
  // with no explicit deal id should act on; wireEvents clears it on close.
  docContextDealId: null,
  // Every displayed value comes from a board snapshot this coordinator applied;
  // the changes feed drives receipts, presence and capture, and asks for a new
  // snapshot, but never writes a value. See board-sync.mjs.
  boardSync: null,
  mode: 'fixture',
  workspace: 'team', accountId: null, filter: 'active', query: '', deepLinkMine: false,
  changed: new Set(),
  // Per cell, the newest event this session has SEEN for it — `{id, recorded_at}`
  // — which is the base its next write will send. THREE sources and one rule:
  // the authoritative board read, which now carries each editable cell's latest
  // committed event beside its value; the changes feed; and the answer to a write
  // this board made. All of them go through noteCellBase, which only ever moves a
  // base forward, so a read that was already open when a write was confirmed
  // cannot walk it back onto the event that write superseded.
  //
  // A cell with no history has no entry, and its write correctly sends null —
  // which the record layer reads as "any prior event conflicts", and on a cell
  // with no prior event that is simply no conflict. Nothing here is invented: an
  // absent base is absent, never a guess.
  fieldBase: new Map(),
  // One entry per edited cell, and only while its write is unanswered: the
  // request as it was sent — value, base and idempotency key — so a retry is the
  // SAME operation and not a second one. Never a value store; the board's values
  // come from a snapshot. See field-write-reconciliation.mjs.
  fieldWrites: createFieldWriteState(),
  presence: [], captureSessions: [],
  confirms: [], review: null, pollTimer: null, boardRefreshTimer: null,
  // Recent changes are session memory only: bounded, never stored, and never
  // a substitute for the deal's own Change history. The feed cursor starts at
  // the beginning of the log, so nothing is shown until it reaches the present.
  receipts: [], undo: createUndoState(), feed: createFeedProgress(),
  receiptSignature: null, receiptFocus: null, receiptAnnounced: null,
  // What the unconfirmed-changes bar last drew, so a poll does not rewrite it —
  // a render memo, exactly like receiptSignature. The operations themselves live
  // in fieldWrites and nowhere else.
  pendingSignature: null,
  // No call state of any kind lives here. Recording is inactive in this release
  // and this shell holds nothing that could start, stop, time or follow one.
  // What the record layer already knows about past capture sessions still
  // arrives on the ordinary feed above, and is only ever displayed.
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const today = () => new Date(new Date().toDateString());
const esc = escapeText;
const actorName = (slug) => ACTOR_LABEL[slug] || slug || 'Unassigned';
const account = () => state.accounts.find((item) => item.account_client_id === state.accountId) || null;

function daysFromNow(value) {
  if (!value) return null;
  return Math.round((new Date(`${value}T12:00:00`) - today()) / 864e5);
}

function dateLabel(value) {
  if (!value) return 'Not set';
  const days = daysFromNow(value);
  const date = new Date(`${value}T12:00:00`).toLocaleDateString('en-US', { month:'short', day:'numeric' });
  if (days < 0) return `${date} · ${Math.abs(days)}d overdue`;
  if (days === 0) return 'Today';
  if (days === 1) return 'Tomorrow';
  return `${date} · ${days}d`;
}

function relative(value) {
  if (!value) return 'not captured';
  const days = Math.round((Date.now() - new Date(value).getTime()) / 864e5);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  return `${days} days ago`;
}

function isStale(deal) {
  if (!deal.last_touch) return true;
  return (Date.now() - new Date(`${deal.last_touch}T12:00:00`).getTime()) / 864e5 >= 14;
}

function reasonFor(deal) {
  if (deal.operating_state === 'parked') return parkingReasonLabel(deal.parking_reason);
  const days = daysFromNow(deal.next_date);
  if (deal.attention) return 'Flagged for attention';
  if (days !== null && days < 0) return `Next date is ${Math.abs(days)} day${Math.abs(days) === 1 ? '' : 's'} overdue`;
  if (!deal.next_step) return 'No next step';
  if (isStale(deal)) return deal.last_touch ? `Gone quiet ${relative(deal.last_touch)}` : 'No recent touch captured';
  if (!deal.market_agent && deal.workspace_kind === 'national_account') return 'Market agent unassigned';
  return `Ready for review · ${deal.phase}`;
}

function priority(deal) {
  const days = daysFromNow(deal.next_date);
  return (deal.attention ? 10000 : 0) + (days !== null && days < 0 ? 7000 + Math.abs(days) : 0)
    + (!deal.next_step ? 4000 : 0) + (isStale(deal) ? 2000 : 0)
    + (deal.workspace_kind === 'national_account' && !deal.market_agent ? 1000 : 0);
}

function showToast(message, undoEventId = null) {
  const el = $('#toast');
  // The toast's Undo is the same event-specific action as the receipt row's:
  // it names its event, and both go through runUndo.
  el.innerHTML = `${esc(message)}${undoEventId ? `<button type="button" data-undo="${esc(undoEventId)}">Undo</button>` : ''}`;
  el.classList.add('show');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => el.classList.remove('show'), undoEventId ? 7000 : 3200);
}

function clockLabel(ms) {
  const at = new Date(ms);
  return `${String(at.getHours()).padStart(2,'0')}:${String(at.getMinutes()).padStart(2,'0')}:${String(at.getSeconds()).padStart(2,'0')}`;
}

/**
 * Where the freshness sentence lives so a keyboard or a screen reader can get
 * at it. `title` is hover-only, which makes it invisible to both.
 *
 * The badge becomes focusable and is DESCRIBED by a visually hidden sibling —
 * a sibling, not a child, because #syncStatus is a live region and a timestamp
 * changing inside it would be read out on every poll. Built once, from here,
 * using the stylesheet's existing `.sr-only` and `:focus-visible`: no markup
 * file changes and nothing about the layout moves.
 *
 * Known limit, named rather than papered over: this reaches assistive
 * technology and the keyboard, not a SIGHTED touch user, who still has no way
 * to summon the detail. That needs a visible disclosure in index.html/app.css,
 * which is outside this unit.
 */
function syncDetailNode() {
  const existing = $('#syncFreshness');
  if (existing) return existing;
  const badge = $('#syncStatus');
  const node = document.createElement('span');
  node.id = 'syncFreshness';
  node.className = 'sr-only';
  node.setAttribute('aria-live', 'off');
  badge.insertAdjacentElement('afterend', node);
  badge.setAttribute('tabindex', '0');
  badge.setAttribute('aria-describedby', 'syncFreshness');
  return node;
}

/**
 * What is true of each read path, in a sentence each. The board and the feed
 * are reported separately because they fail separately: a feed that answers
 * says nothing about whether the values are current.
 */
function syncDetail(status) {
  const at = status.last_read_at ? clockLabel(status.last_read_at) : null;
  const board = status.board_health === HEALTH.OK
    ? `Board values are from the last successful read at ${at}; they change when the next read succeeds.`
    : status.board_health === HEALTH.RENDER_FAILED
      ? `The last board snapshot could not be shown${status.board_error ? ` (${status.board_error})` : ''}, so what is on screen is older than the answer that arrived.`
      : at
        ? `The board could not be re-read${status.board_error ? ` (${status.board_error})` : ''}. Values are from the last successful read at ${at}.`
        : 'No board read has succeeded yet in this session.';
  const feed = status.feed_health === HEALTH.OK
    ? 'The change feed answered on its last read.'
    : status.feed_health === HEALTH.FAILED
      ? `The change feed is not answering${status.feed_error ? ` (${status.feed_error})` : ''}, so new receipts and presence may be missing.`
      : 'The change feed has not been read yet.';
  return `${board} ${feed}`;
}

/**
 * The connection badge, derived from the coordinator's two health signals. It
 * says what is true of the READS — not that the screen matches the record
 * layer, which no client can know.
 */
function setSync(status) {
  const el = $('#syncStatus');
  const live = state.mode === 'live';
  const label = status.state === SYNC_STATES.OFFLINE ? 'Offline'
    : status.state === SYNC_STATES.ERROR ? 'Board view error'
    : status.state === SYNC_STATES.RECONNECTING ? (live ? 'Reconnecting' : 'Fixture unavailable')
    : status.state === SYNC_STATES.READY ? (live ? 'Live sync' : 'Fixture ready')
    : 'Syncing…';
  el.classList.toggle('offline',
    [SYNC_STATES.OFFLINE, SYNC_STATES.RECONNECTING, SYNC_STATES.ERROR].includes(status.state));
  // role="status" is a live region: writing the same words again can read them
  // out again, so the text moves only when it actually changed.
  if (el.textContent !== label) el.textContent = label;
  const detail = syncDetail(status);
  el.title = detail;
  syncDetailNode().textContent = detail;
}

/**
 * Read the board authoritatively, then take one page of the changes feed.
 *
 * A failed read leaves the previous values on screen and says so in the badge —
 * except on the very first load, where there is nothing to leave: an empty
 * board would read as "no work records", which is a different claim from "the
 * board could not be read", so that one is raised.
 */
async function loadHome() {
  const outcome = await state.boardSync.refreshBoard({ reason:'load-home' });
  if (!outcome.applied && state.boardSync.status().snapshots === 0) {
    throw outcome.error || new Error('The board did not answer.');
  }
  await pollOnce(true);
  render();
}

function applyBoardSnapshot(home) {
  state.selfActor = home.actor || state.client.selfActor || state.selfActor;
  state.deals = new Map((home.deals || []).map((deal) => [deal.id, {
    workspace_kind: deal.account_client_id ? 'national_account' : 'team', ...deal,
  }]));
  // The base for each editable cell, from the same read as the values — so the
  // FIRST edit of a session sends what the record layer actually holds instead
  // of nothing, which that layer reads as "any prior event conflicts".
  //
  // Through noteCellBase, which is forward-only, because a snapshot is not
  // always the newest thing this page knows: a read that was already open when a
  // write was confirmed carries that cell's OLDER base, and its value is being
  // held over on the row for the same reason. Taking it would walk this cell's
  // base backwards and make the next edit collide with our own committed one.
  for (const deal of state.deals.values()) {
    for (const [field, seen] of Object.entries(deal.field_base || {})) {
      noteCellBase(cellKey(deal.id, field), seen);
    }
  }
  state.accounts = home.accounts || [];
  for (const deal of state.deals.values()) if (!deal.last_review_at) state.changed.add(deal.id);
  $('#selfAvatar').textContent = state.selfActor === 'dell' ? 'D' : state.selfActor === 'joe' ? 'J' : '?';
  $('#selfAvatar').setAttribute('aria-label', `Signed in as ${actorName(state.selfActor)}`);
}

async function pollOnce(initial = false, { force = false } = {}) {
  const outcome = await state.boardSync.pollChanges({ force });
  // Skipped (a poll was already open) or superseded (the answer stopped being
  // current before it arrived). Neither is news and neither moves the cursor.
  if (!outcome.applied) {
    if (outcome.reason === 'read_failed') console.warn('Deal Room poll failed', outcome.error);
    return;
  }
  const result = outcome.changes;
  state.presence = result.presence || [];
  state.captureSessions = result.capture_sessions || [];
  renderCaptureStatus();
  const batch = result.events || [];
  state.feed = observeChangeBatch(state.feed, batch);
  // Until the cursor reaches the present, these are pages of history, not
  // news: no toast announces them and the panel stays closed.
  const announce = !initial && state.feed.caught_up;
  for (const event of batch) {
    // The base for the NEXT write to this cell. It never rewrites a write that
    // is already out and unanswered: that request is a statement about what was
    // on screen when the person acted, and replay depends on it unchanged.
    if (event.field) noteCellBase(cellKey(event.subject_id, event.field), event);
    const deal = state.deals.get(event.subject_id);
    if (!deal) continue;
    if (!deal.last_review_at || String(event.recorded_at) > String(deal.last_review_at)) state.changed.add(deal.id);
    // NO VALUE IS TAKEN FROM AN EVENT. The feed is an ascending cursor over the
    // whole log, so an early page holds values that are months old; writing them
    // onto a deal put history back on the board. What an event does is mark the
    // record as changed, name the base for the next write, and — below — ask for
    // a fresh authoritative snapshot.
    if (announce && event.actor === state.selfActor && REVERTIBLE_FIELDS.includes(event.field)) {
      showToast(`${deal.name} updated`, event.id);
    } else if (announce && event.actor !== state.selfActor && event.field) {
      showToast(`${actorName(event.actor)} updated ${deal.name}`);
    }
  }
  // News, not history: once the cursor is current, anything about a deal — including
  // one this board has never seen, which is what a new record looks like to an
  // empty board — is a reason to re-read the board. Requests coalesce into one
  // read, so a burst of partner edits costs one refresh, not one per event.
  if (state.feed.caught_up && batchTouchesBoard(batch)) state.boardSync.requestRefresh('change-feed');
  // Same events, kept instead of discarded once the toast fades. No second
  // read: this is the batch the board just consumed.
  state.receipts = ingestChangeEvents(state.receipts, batch, {
    dealName: (dealId) => state.deals.get(dealId)?.name || null,
    actorLabel: actorName,
  });
  renderReceipts();
  if (state.client.getPendingConfirms) {
    // A separate read with a separate failure: a capture queue that will not
    // answer says nothing about the board, so it leaves the last set of chips
    // alone instead of claiming the connection is gone.
    try {
      const pending = await state.client.getPendingConfirms();
      state.confirms = pending.proposals || [];
      renderConfirms();
    } catch (error) {
      console.warn('Deal Room confirm queue read failed', error);
    }
  }
  if (!userIsEditing()) renderBoardOnly();
}

function userIsEditing() {
  return Boolean(document.querySelector('dialog[open]') || document.activeElement?.matches('input,textarea,select'));
}

/**
 * A full render that does not throw the keyboard on the floor.
 *
 * `userIsEditing` guards dialogs and form fields; a focused BUTTON is invisible
 * to it, and the National Accounts home is nothing but buttons that
 * `renderAccounts` replaces wholesale. That grid never used to re-render on its
 * own — the poll only called `renderBoardOnly`, which returns immediately while
 * the board section is hidden — but a snapshot now lands on a 15-second timer,
 * so a card under the keyboard would be destroyed with no user action at all.
 *
 * The card is restored by its `data-account`, the same way an Undo button is
 * restored by its event id after the receipts list is rebuilt: only what was
 * taken is given back, the page is never scrolled to do it, and a card the
 * board no longer returns is not replaced with a different one — focus stays
 * where the browser put it rather than landing somewhere unrelated.
 */
function renderPreservingFocus() {
  const grid = $('#accountGrid');
  const active = document.activeElement;
  const focused = grid && active && grid.contains(active)
    ? active.closest('[data-account]')?.dataset.account || null
    : null;
  render();
  if (!focused) return;
  $(`[data-account="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
}

function workspaceDeals() {
  let deals = [...state.deals.values()];
  if (state.query) {
    const query = state.query.toLowerCase();
    deals = deals.filter((deal) => [deal.name,deal.client_name,deal.account_name,deal.market,
      deal.market_agent,deal.next_step,deal.segment].some((value) => String(value || '').toLowerCase().includes(query)));
  } else if (state.workspace === 'team') {
    deals = deals.filter((deal) => deal.workspace_kind === 'team');
  } else if (state.accountId) {
    deals = deals.filter((deal) => deal.account_client_id === state.accountId);
  } else {
    deals = [];
  }
  if (state.filter === 'parked') deals = deals.filter((deal) => deal.operating_state === 'parked');
  else deals = deals.filter((deal) => (deal.operating_state || 'active') === 'active');
  if (state.filter === 'mine') deals = deals.filter((deal) => deal.owner === state.selfActor);
  if (state.filter === 'attention') deals = deals.filter((deal) => deal.attention || (daysFromNow(deal.next_date) ?? 0) < 0);
  if (state.filter === 'flagged') deals = deals.filter((deal) => deal.attention === true && (!state.deepLinkMine || deal.owner === state.selfActor));
  if (state.filter === 'stale') deals = deals.filter(isStale);
  if (state.filter === 'missing') deals = deals.filter((deal) => !deal.next_step);
  if (state.filter === 'delta') deals = deals.filter((deal) => state.changed.has(deal.id));
  return deals.sort((a,b) => priority(b) - priority(a) || a.name.localeCompare(b.name));
}

function render() {
  renderChrome();
  renderAccounts();
  renderBoardOnly();
}

function renderChrome() {
  $$('.workspace').forEach((button) => button.classList.toggle('on', button.dataset.workspace === state.workspace));
  $$('.filter').forEach((button) => button.classList.toggle('on', button.dataset.filter === state.filter));
  const selected = account();
  const isAccountHome = state.workspace === 'national_account' && !state.accountId && !state.query;
  $('#accountBack').hidden = !state.accountId;
  $('#workspaceEyebrow').textContent = state.query ? 'Global Deal Room search'
    : state.workspace === 'team' ? 'Shared territory pipeline' : selected ? 'National account agenda' : 'Partner-owned portfolios';
  $('#workspaceTitle').textContent = state.query ? 'Search results' : state.workspace === 'team' ? 'Team Book'
    : selected?.account_name || 'National Accounts';
  $('#workspaceSubtitle').textContent = state.query ? 'Searching work records across the territory and every national account.'
    : state.workspace === 'team' ? 'The active work Joe and Dell are moving now.'
    : selected ? `${actorName(selected.account_owner)} owns the account; each market deal keeps its assigned agent and owner.`
    : 'One account can hold dozens of market-level transactions without crowding the territory agenda.';
  const addLabel = state.workspace === 'national_account' ? (selected ? 'Add market deal' : 'Add national account') : 'Add work record';
  $('#stickyAddButton').textContent = `+ ${addLabel}`;
  $('#stickyAddButton').setAttribute('aria-label', addLabel);
  $('#ownerButton').hidden = !selected;
  $('#agendaButton').hidden = isAccountHome;
  const agentHeading = state.workspace === 'national_account' ? 'Market agent' : 'Owner';
  $('#agentHeading').textContent = agentHeading;
  $('#stickyAgentHeading').textContent = agentHeading;
  $('#accountGrid').hidden = !isAccountHome;
  $('#boardSection').hidden = isAccountHome;
}

function renderAccounts() {
  const grid = $('#accountGrid');
  grid.innerHTML = state.accounts.length ? state.accounts.map((item) => `
    <button type="button" class="account-card" data-account="${esc(item.account_client_id)}">
      <header><div><p class="eyebrow">${esc(item.account_client_ref || 'National account')}</p><h2>${esc(item.account_name)}</h2></div>
        <span class="account-owner" title="Owned by ${esc(actorName(item.account_owner))}">${item.account_owner === 'dell' ? 'D' : item.account_owner === 'joe' ? 'J' : '?'}</span></header>
      <div class="account-metrics"><div><b>${Number(item.open_deals || 0)}</b><span>Active work</span></div>
        <div><b>${Number(item.attention_deals || 0)}</b><span>Attention</span></div>
        <div><b>${Number(item.stale_deals || 0)}</b><span>Gone quiet</span></div></div>
      <footer>${Number(item.parked_deals || 0)} parked · Last account review: ${esc(relative(item.last_review_at))} · Open agenda →</footer>
    </button>`).join('') : '<div class="empty">No national accounts yet. Add the first portfolio when it is won.</div>';
}

/**
 * Where an unconfirmed change can always be found.
 *
 * The row's own Retry is the natural place for one, and it is the place a filter,
 * a search or a workspace switch takes away — so the sentence that says "send it
 * again" was pointing at a control the person might not be able to see. This bar
 * is the answer to that: one line per operation this page has not had an answer
 * to, above the workspace, outside every filter, naming the actual record and the
 * actual cell, carrying the SAME deliberate retry as everywhere else.
 *
 * Built here rather than in the markup, the way the freshness detail is: it is
 * the one control whose existence depends on something having gone wrong.
 */
function pendingWritesNode() {
  const existing = $('#pendingWrites');
  if (existing) return existing;
  const main = $('#main');
  if (!main) return null;
  const node = document.createElement('section');
  node.id = 'pendingWrites';
  node.className = 'parking-banner';
  // A status region, not an alert: it is a standing list, and it is only rewritten
  // when the set of unconfirmed operations actually changes — so a poll every
  // 1.4 seconds neither re-announces it nor destroys the button under a finger.
  node.setAttribute('role', 'status');
  node.setAttribute('aria-label', 'Unconfirmed changes');
  main.insertAdjacentElement('afterbegin', node);
  return node;
}

function renderPendingWrites() {
  const node = pendingWritesNode();
  if (!node) return;
  const pending = unresolvedFieldWrites(state.fieldWrites);
  node.hidden = pending.length === 0;
  const signature = JSON.stringify(pending.map((entry) => [entry.cell, state.deals.get(entry.deal)?.name || '']));
  if (signature === state.pendingSignature) return;
  state.pendingSignature = signature;
  if (!pending.length) { node.innerHTML = ''; return; }
  node.innerHTML = `<b>Unconfirmed changes · ${pending.length}</b>`
    + pending.map((entry) => {
      // The record as this board actually knows it. A deal the last snapshot did
      // not return is not given a borrowed name: the cell is still nameable and
      // the operation is still re-sendable, and Open record still reaches it,
      // because a detail read asks the record layer by id.
      const deal = state.deals.get(entry.deal);
      const name = deal?.name || 'a record this board is not showing right now';
      return `<span>${esc(fieldLabel(entry.field))} on ${esc(name)} — not confirmed
        <button type="button" class="park-button" data-retry-write="${esc(entry.cell)}"
          aria-label="Send the unconfirmed ${esc(fieldLabel(entry.field))} change on ${esc(name)} again">Send again</button>
        <button type="button" class="park-button" data-open-deal="${esc(entry.deal)}"
          aria-label="Open ${esc(name)} and check what it holds">Open record</button></span>`;
    }).join('');
}

function renderBoardOnly() {
  // Before the board's own visibility is considered: an unconfirmed change
  // belongs to the page, not to whichever workspace or filter is showing.
  renderPendingWrites();
  if ($('#boardSection').hidden) return;
  const deals = workspaceDeals();
  renderStats(deals);
  renderFocus(deals);
  const rows = $('#rows');
  rows.innerHTML = deals.map(rowHtml).join('');
  $('#emptyState').hidden = deals.length > 0;
  $('#emptyState').textContent = state.query ? 'No work record matches this search.'
    : state.filter === 'parked' ? 'No parked work records.' : 'No active work matches this filter.';
  applyPresence();
}

function renderStats(deals) {
  if (state.filter === 'parked') {
    $('#stats').innerHTML = `
      <div class="stat"><strong>${deals.length}</strong><span>Parked records</span></div>
      <div class="stat"><strong>${deals.filter((deal) => deal.parking_reason === 'prospect_never_active').length}</strong><span>Never activated</span></div>
      <div class="stat"><strong>${deals.filter((deal) => deal.parking_reason === 'client_paused').length}</strong><span>Client paused</span></div>
      <div class="stat"><strong>${deals.filter((deal) => deal.parking_reason === 'other').length}</strong><span>Other reason</span></div>`;
    return;
  }
  const overdue = deals.filter((deal) => (daysFromNow(deal.next_date) ?? 0) < 0).length;
  const clarity = deals.filter((deal) => !deal.next_step || isStale(deal)).length;
  $('#stats').innerHTML = `
    <div class="stat"><strong>${deals.length}</strong><span>Active work</span></div>
    <div class="stat"><strong>${deals.filter((deal) => deal.phase === 'Closing').length}</strong><span>At closing</span></div>
    <div class="stat risk"><strong>${overdue}</strong><span>Overdue dates</span></div>
    <div class="stat"><strong>${clarity}</strong><span>Need clarity</span></div>`;
}

function renderFocus(deals) {
  if (state.filter === 'parked') { $('#focusStrip').innerHTML = ''; return; }
  const items = deals.filter((deal) => priority(deal) > 0).slice(0, 4);
  $('#focusStrip').innerHTML = items.length ? '<span class="focus-label">Focus first</span>' + items.map((deal) => `
    <button type="button" class="focus-item" data-open-deal="${esc(deal.id)}"><b>${esc(deal.name)}</b><span>${esc(reasonFor(deal))}</span></button>`).join('') : '';
}

function phaseOptions(current) {
  return PHASES.map((phase) => `<option${phase === current ? ' selected' : ''}>${esc(phase)}</option>`).join('');
}

function rowHtml(deal) {
  const days = daysFromNow(deal.next_date);
  const parked = deal.operating_state === 'parked';
  const classes = [parked ? 'parked' : '', deal.attention ? 'attention' : '', days !== null && days < 0 ? 'overdue' : ''].join(' ');
  const partnerPresence = state.presence.find((lease) => lease.deal_id === deal.id && lease.actor !== state.selfActor && new Date(lease.expires_at) > new Date());
  const meta = [deal.market, deal.type, state.query && deal.account_name ? deal.account_name : null,
    parked ? parkingReasonLabel(deal.parking_reason) : null,
    partnerPresence ? `${actorName(partnerPresence.actor)} is editing` : null].filter(Boolean);
  // A write this board never got an answer to stays on its row until a person
  // settles it. The control re-sends THAT request — same key — rather than
  // guessing a new one, and it is the row's own text, not a toast that fades and
  // not a title only a mouse can find.
  const unconfirmed = unresolvedFieldWrites(state.fieldWrites, deal.id).map((entry) => `<button type="button"
    class="park-button" data-retry-write="${esc(entry.cell)}"
    aria-label="${esc(fieldLabel(entry.field))} on ${esc(deal.name)} was not confirmed — send the same change again">${esc(fieldLabel(entry.field))} not confirmed · Retry</button>`).join('');
  return `<tr class="${classes}" data-deal-id="${esc(deal.id)}">
    <td><div class="deal-cell"><button type="button" class="attention-button" data-attention="${esc(deal.id)}" aria-pressed="${Boolean(deal.attention)}" aria-label="${deal.attention ? 'Clear attention flag' : 'Flag for attention'} on ${esc(deal.name)}"${parked ? ' disabled' : ''}>${deal.attention ? '⚠' : (PHICON[deal.phase] || '○')}</button>
      <div><button type="button" class="deal-link" data-open-deal="${esc(deal.id)}">${esc(deal.name)}</button>
      <div class="deal-meta">${meta.map((item) => `<span>${esc(item)}</span>`).join('<span>·</span>')}</div></div></div></td>
    <td><select class="cell-select" data-phase="${esc(deal.id)}" aria-label="Phase for ${esc(deal.name)}"${parked ? ' disabled' : ''}>${phaseOptions(deal.phase)}</select></td>
    <td><button type="button" class="step-button ${deal.next_step ? '' : 'empty'}" data-next-step="${esc(deal.id)}"${parked ? ' disabled' : ''}>${esc(deal.next_step || 'Set the next step…')}</button></td>
    <td><span class="due ${days !== null && days < 0 ? 'over' : days !== null && days <= 3 ? 'soon' : ''}">${esc(dateLabel(deal.next_date))}</span></td>
    <td>${deal.workspace_kind === 'national_account'
      ? `<button type="button" class="agent-button" data-market-agent="${esc(deal.id)}"${parked ? ' disabled' : ''}>${esc(deal.market_agent || 'Assign agent…')}</button>`
      : `<select class="cell-select" data-owner="${esc(deal.id)}" aria-label="Owner for ${esc(deal.name)}"${parked ? ' disabled' : ''}><option value="">Unassigned</option><option value="joe"${deal.owner === 'joe' ? ' selected' : ''}>Joe</option><option value="dell"${deal.owner === 'dell' ? ' selected' : ''}>Dell</option></select>`}</td>
    <td class="row-actions">${unconfirmed}<button type="button" class="park-button" data-operating-state="${parked ? 'active' : 'parked'}" data-deal="${esc(deal.id)}">${parked ? 'Restore' : 'Park'}</button><button type="button" class="row-menu" data-open-deal="${esc(deal.id)}" aria-label="Open ${esc(deal.name)} details">•••</button></td>
  </tr>`;
}

function applyPresence() {
  for (const lease of state.presence) {
    if (lease.actor === state.selfActor || new Date(lease.expires_at) <= new Date()) continue;
    const row = document.querySelector(`[data-deal-id="${CSS.escape(lease.deal_id)}"]`);
    if (row) row.title = `${actorName(lease.actor)} is editing ${lease.field}`;
  }
}

function renderCaptureStatus() {
  const active = state.captureSessions.find((session) => !['done','completed','failed','cancelled'].includes(session.state));
  const badge = $('#captureStatus');
  badge.hidden = !active;
  if (active) badge.textContent = `Capture: ${String(active.state).replaceAll('_',' ')}`;
}

/**
 * Calls are inactive in this release, and this module is where that is TRUE
 * rather than merely intended.
 *
 * There is no recorder address, no start, no stop, no recorder-state read and
 * no poll of a local bridge anywhere in this file. The entrypoint opens a dialog
 * that says so, and does nothing else: no request leaves the page, no timer
 * starts, and no synthetic click, boot callback, query parameter or stored
 * preference can reach a recording path, because this workspace no longer
 * contains one.
 *
 * Nothing already recorded is affected. The recorder, its standalone controller
 * and every saved call, action and summary are untouched; what the record layer
 * holds about them still arrives through the same board read, change feed and
 * suggestions dock as any other work, and is only ever displayed.
 */

/**
 * Controls that belonged to the retired in-shell recorder.
 *
 * A stale index.html can outlive the module that goes with it: the service
 * worker caches a navigation and a script separately, so an installed app can
 * pair yesterday's markup with today's code. Such a control arrives with no
 * handler and is already inert — but it would still LOOK live, and a person
 * pressing "Start weekly deal call" deserves better than a button that silently
 * does nothing. They are disabled and hidden on sight, and never wired.
 */
const RETIRED_RECORDER_CONTROLS = [
  '#callModeButton', '#callModeStop', '[data-call-mode-start]', '#postCallRefresh',
  '[data-post-call-confirm]', '[data-post-call-skip]', '[data-create-outlook-draft]',
  '[data-retry-call-context]',
];

function neutralizeRetiredRecorderControls(root) {
  const found = RETIRED_RECORDER_CONTROLS.flatMap((selector) => $$(selector, root));
  // A link to a local controller is an entrypoint too, even though a person has
  // to press it: this workspace does not offer one.
  for (const link of $$('a', root)) {
    if (/127\.0\.0\.1|localhost/i.test(link.getAttribute('href') || '')) found.push(link);
  }
  for (const node of found) {
    node.disabled = true;
    node.hidden = true;
    node.setAttribute('aria-hidden', 'true');
    if (node.getAttribute('href') !== null) node.removeAttribute('href');
  }
  return found;
}

/**
 * Wire the Calls entrypoint: one dialog, opened and closed, and nothing else.
 *
 * Exported so the boundary can be exercised as BEHAVIOUR — installed on a
 * document, pressed, and observed to make no request and start no timer —
 * rather than only read as source.
 */
export function installCallsBoundary(root = document) {
  const neutralized = neutralizeRetiredRecorderControls(root);
  const button = $('#callsButton', root);
  const dialog = $('#callsDialog', root);
  const close = $('#callsClose', root);
  if (button && dialog) button.addEventListener('click', () => {
    if (typeof dialog.showModal === 'function' && !dialog.open) dialog.showModal();
  });
  if (close && dialog) close.addEventListener('click', () => {
    if (dialog.open) dialog.close();
  });
  return { entrypoint: Boolean(button), dialog: Boolean(dialog), neutralized: neutralized.length };
}

function renderConfirms() {
  const dock = $('#confirmDock');
  dock.hidden = !state.confirms.length;
  dock.innerHTML = state.confirms.map((proposal) => `<span class="confirm-chip" data-proposal="${esc(proposal.id)}">${esc(proposal.label)}
    <button type="button" class="yes" data-confirm="yes">Confirm</button><button type="button" class="skip-confirm" data-confirm="no">Skip</button></span>`).join('');
}

function renderReceipts() {
  const panel = $('#receiptsPanel');
  if (!panel) return;
  const list = $('#receiptsList');
  const jump = $('#receiptsJump');
  // Nothing is shown while the cursor is still working through history: an old
  // page is not "recent", and its newest row is not safely undoable.
  const views = state.feed.caught_up
    ? receiptViews(state.receipts, { selfActor: state.selfActor, undo: state.undo, now: Date.now() })
    : [];
  panel.hidden = views.length === 0;
  // The board is long. A counted control sits with the other board controls so
  // the panel below the table is findable without scrolling for it.
  jump.hidden = views.length === 0;
  $('#receiptsCount').textContent = String(views.length);
  jump.setAttribute('aria-label', `Go to recent changes — ${views.length} seen in this session`);
  // Read the focus request whether or not this render proceeds, so a skipped
  // render cannot leave it to be spent on an unrelated one later.
  const active = document.activeElement;
  const keep = state.receiptFocus
    || (active && list.contains(active) ? active.closest('[data-receipt]')?.dataset.receipt : null)
    || null;
  state.receiptFocus = null;
  const signature = receiptsSignature(views);
  if (signature === state.receiptSignature) return;
  state.receiptSignature = signature;
  // Re-render only on a real change, so an Undo button under the partner's
  // finger keeps its focus. The list is NOT a live region — announcing it
  // wholesale would read all 25 rows back for one change — so what actually
  // arrived is named on its own short status line.
  // Renders while the list is closed hold no news and leave the baseline
  // unset, so the render that finally opens it announces nothing either.
  const opened = views.length > 0;
  const announcement = opened ? receiptsAnnouncement(state.receiptAnnounced, views) : '';
  if (opened) state.receiptAnnounced = views.map((view) => view.event_id);
  $('#receiptsLive').textContent = announcement;
  list.innerHTML = receiptListHtml(views);
  if (keep) focusReceipt(keep);
}

/**
 * Land the keyboard somewhere sensible after a row is rebuilt. The Undo button
 * is gone once the change is undone or refused, and is disabled while pending,
 * so the row itself is the next best position; the panel heading is the last
 * resort when the row has fallen off the end of the list.
 */
function focusReceipt(eventId) {
  const list = $('#receiptsList');
  const target = $(`[data-undo="${CSS.escape(eventId)}"]`, list)
    || $(`[data-receipt="${CSS.escape(eventId)}"]`, list)
    || $('#receiptsTitle');
  // Restoring focus must not yank the page: this runs on poll-driven renders.
  target?.focus({ preventScroll: true });
}

function goToReceipts() {
  const panel = $('#receiptsPanel');
  if (panel.hidden) return;
  panel.scrollIntoView({ block: 'start' });
  $('#receiptsTitle').focus({ preventScroll: true });
}

/**
 * A write the server has already confirmed, applied to the page.
 *
 * Four things happen together, and the order is the point:
 *   1. the value lands on the CURRENT board row, looked up now — a row captured
 *      before a snapshot arrived is no longer the object being rendered;
 *   2. it is HELD, so a board read that was already open when the write was
 *      confirmed cannot put the old value back when it answers;
 *   3. the record is marked as changed for the delta filter;
 *   4. a fresh authoritative read is REQUESTED, never awaited — acknowledgement
 *      is immediate and the re-read follows it.
 *
 * @returns the live deal row, or null if the board does not hold it.
 */
function confirmLocalWrite(dealId, patch) {
  const deal = state.deals.get(dealId) || null;
  if (deal) Object.assign(deal, patch);
  state.boardSync.noteLocalWrite(dealId, patch);
  state.changed.add(dealId);
  state.boardSync.requestRefresh('after-write');
  return deal;
}

/**
 * One undo, one event, one in-flight request, one idempotency key.
 *
 * Nothing about the deal is changed here before the server answers, and the
 * row is marked undone only on a confirmed success. A refusal stays on the
 * row; an unconfirmed answer is left unconfirmed rather than retried.
 */
async function runUndo(eventId, trigger = null) {
  if (!eventId) return;
  // Focus is only moved when the click came from the list itself; a toast Undo
  // must not drag the keyboard down the page.
  const fromList = Boolean(trigger && $('#receiptsList')?.contains(trigger));
  const result = await performUndo({
    eventId,
    getState: () => state.undo,
    setState: (next) => { state.undo = next; if (fromList) state.receiptFocus = eventId; renderReceipts(); },
    newKey: uuidv4,
    revert: (request) => state.client.revertDealField(request),
  });
  if (!result.started) return;
  if (result.outcome.status === 'succeeded') {
    await loadHome();
    showToast('Change undone');
    return;
  }
  showToast(result.outcome.message);
}

/**
 * Send one cell change — or put the one that was never answered back out.
 *
 * The key is minted per intended action, not per attempt, and the retained
 * request is sent unchanged: same value, same base, same key. That is what makes
 * a retry a replay at the server instead of a second write that collides with
 * the first one and manufactures a conflict between a person and themselves.
 * Nothing here decides anything about the deal; the answer does.
 */
async function sendCellWrite(dealId, field, value) {
  const cell = cellKey(dealId, field);
  const result = await performFieldWrite({
    deal: dealId, field, value,
    base: state.fieldBase.get(cell)?.id || null,
    // Read AGAIN when the answer lands. If the base moved while the request was
    // out, the answer — a replayed one especially — is the recorded result of an
    // operation the board has since learned something newer about, and its value
    // must not be painted over what it learned.
    baseNow: () => state.fieldBase.get(cell)?.id || null,
    getState: () => state.fieldWrites,
    setState: (next) => { state.fieldWrites = next; },
    newKey: uuidv4,
    patch: (request) => state.client.patchDealField(request),
  });
  // The base for the NEXT write to this cell, taken from the record's own answer
  // about this one instead of waiting a poll for the feed to say the same thing.
  // That wait is what made a second, different edit inside the poll window send a
  // base its own first edit had already superseded — a conflict with itself.
  //
  // Only an accepted answer names an event. `superseded` is checked as well as
  // ordered against: an answer replayed from an older operation must not reset
  // the base even if the record layer sent no time to order it by.
  if (result.status === 'ok' && !result.superseded && result.event_id) {
    noteCellBase(cell, { id: result.event_id, recorded_at: result.event_recorded_at });
  }
  return result;
}

/**
 * Record the newest event seen for one cell.
 *
 * One home for the rule, because there are now two sources: the changes feed —
 * partner events, and this board's own arriving late — and the answer to a write
 * this board made. `nextCellBase` keeps whichever is newer by `(recorded_at, id)`,
 * the record layer's own ordering, so an old page of catch-up history and a
 * replayed answer about an older operation both leave a newer base alone.
 */
function noteCellBase(cell, event) {
  if (!event?.id) return;
  // Identity and time only. A feed event carries values too, and none of them
  // belong in this map — the board's values come from a snapshot, and this is a
  // base, not a value.
  const seen = { id: event.id, recorded_at: event.recorded_at ?? null };
  state.fieldBase.set(cell, nextCellBase(state.fieldBase.get(cell) || null, seen));
}

/** The cell, named the way a person reading a toast about it would name it. */
function cellSubject(dealId, field) {
  return `${fieldLabel(field)} on ${state.deals.get(dealId)?.name || 'this record'}`;
}

/** Where an answer must appear: inside the modal the control was pressed in. */
function writeSurfaceFor(trigger) {
  const dialog = $('#dealDialog');
  return trigger && dialog?.open && dialog.contains(trigger) ? 'dialog' : 'toast';
}

/**
 * Put the answer where the person is actually looking.
 *
 * A dialog opened with showModal() is in the top layer — above every z-index and
 * behind its own backdrop — so a toast raised while #dealDialog is open is
 * dimmed, blurred and unreadable, and the row control that would act on it is
 * behind the same backdrop. The sentence goes INSIDE the dialog instead, and
 * carries the same deliberate retry the row carries: the same operation, the
 * same key, never a fresh one. It takes the keyboard because it is the answer to
 * what was just pressed here.
 *
 * It belongs to the dialog's own content: re-opening a record rebuilds it away,
 * and every settled answer on this path closes the dialog, so nothing has to
 * remember to take it down.
 *
 * @returns true when the notice was placed; false when no deal dialog is open
 *   and the caller should say it the ordinary way.
 */
function showDealDialogNotice(message, retryCell = null) {
  const dialog = $('#dealDialog');
  if (!dialog?.open) return false;
  const host = $('.deal-content', dialog) || $('#dealDetail');
  if (!host) return false;
  let notice = $('#dealNotice', dialog);
  if (!notice) {
    notice = document.createElement('div');
    notice.id = 'dealNotice';
    notice.className = 'parking-banner';
    notice.setAttribute('role', 'alert');
    notice.setAttribute('tabindex', '-1');
    host.prepend(notice);
  }
  notice.innerHTML = `<b>${esc(message)}</b>${retryCell
    ? `<button type="button" class="park-button" data-retry-write="${esc(retryCell)}">Send this change again</button>`
    : ''}`;
  ($('[data-retry-write]', notice) || notice).focus({ preventScroll: true });
  return true;
}

/**
 * Say what happened to a change — and only what is known — where it can be read.
 *
 * A refusal names the server's own reason; an unanswered write says it could not
 * be confirmed and stays retryable under its own key; a cell whose earlier write
 * is still unresolved says so rather than quietly taking a different intent; an
 * answer that arrived after the feed moved says which value the board is showing.
 *
 * Three surfaces, because a modal changes what "visible" means: a form owns its
 * own error line and keeps its draft, a control pressed inside the deal dialog is
 * answered inside that dialog, and everything else is the toast. Whatever the
 * surface, an operation still unresolved carries its retry with it.
 *
 * The board is redrawn first — deliberately without the `userIsEditing()` guard
 * the feed path uses, because the edit being corrected is the one the person just
 * made: a select left showing a value the server never took is the failure this
 * slice exists to remove, and the row's Retry has to appear with the sentence.
 */
function reportCellWrite(dealId, field, result, { surface = 'toast' } = {}) {
  renderBoardOnly();
  if (surface === 'inline' && result.status !== 'ok') return result;
  const message = fieldWriteMessage(result, cellSubject(dealId, field));
  if (!message) return result;
  const retryCell = result.status === 'unknown' || result.status === 'blocked'
    ? result.pending?.cell || null : null;
  if (surface === 'dialog' && showDealDialogNotice(message, retryCell)) return result;
  showToast(message);
  return result;
}

/**
 * The server accepted this operation, and the board has since learned something
 * newer about the same cell.
 *
 * The request's value is NOT written to the row. This cell's base has moved past
 * the one the operation was built on, to an event that operation did not commit —
 * its own event arriving on the feed is recognised and does not come here — so
 * applying the value, and HOLDING it against the next snapshot the way
 * confirmLocalWrite does, would put a stale value on the board and then defend it
 * against the truth.
 *
 * What happens instead is what the board does anywhere else it does not know:
 * mark the record changed, ask for an authoritative read, and say plainly which
 * value is on screen. No event id is invented, nothing is inferred about who
 * wrote what, and the read — not this function — decides what the cell holds.
 */
function reconcileNewerState(dealId, field, result, options = {}) {
  state.changed.add(dealId);
  state.boardSync.requestRefresh('after-write');
  return reportCellWrite(dealId, field, result, options);
}

/**
 * Send an unconfirmed cell change again, exactly as it was sent.
 *
 * Deliberate and person-driven: nothing on a timer re-sends a write nobody asked
 * to re-send. It goes back through the ordinary write path, which is what keeps
 * the key, the base and the value identical.
 */
async function retryCellWrite(cell, trigger = null) {
  const entry = pendingFieldWrite(state.fieldWrites, cell);
  if (!entry) { renderBoardOnly(); return null; }
  const { deal, field, value } = entry.request;
  // The retry is answered on the surface it was asked from: a Retry pressed in
  // the deal dialog is answered in the deal dialog, not under its backdrop.
  const options = { surface: writeSurfaceFor(trigger) };
  if (field === 'operating_state') return patchOperatingState(deal, value, options);
  return patchField(deal, field, value, options);
}

async function patchField(dealId, field, value, options = {}) {
  const result = await sendCellWrite(dealId, field, value);
  if (result.status === 'conflict') return showConflict(result.conflict);
  if (result.status !== 'ok') return reportCellWrite(dealId, field, result, options);
  if (result.superseded) return reconcileNewerState(dealId, field, result, options);
  const deal = confirmLocalWrite(dealId, { [field]: value });
  renderBoardOnly();
  showToast(`${deal?.name || 'Deal'} updated`);
  return result;
}

async function patchOperatingState(dealId, value, options = {}) {
  const result = await sendCellWrite(dealId, 'operating_state', value);
  if (result.status === 'conflict') { showConflict(result.conflict); return result; }
  if (result.status !== 'ok') return reportCellWrite(dealId, 'operating_state', result, options);
  if (result.superseded) {
    // An answer is an answer: the dialog closes exactly as it does on the plain
    // ok path. Only the value is withheld, because the board knows something
    // newer about this record's active-work state than this operation does.
    if ($('#dealDialog').open) $('#dealDialog').close();
    return reconcileNewerState(dealId, 'operating_state', result, options);
  }
  const deal = confirmLocalWrite(dealId, {
    operating_state: value.state,
    parking_reason: value.state === 'parked' ? value.reason : null,
    parking_note: value.state === 'parked' ? value.note || null : null,
    parked_at: value.state === 'parked' ? new Date().toISOString() : null,
    parked_by: value.state === 'parked' ? state.selfActor : null,
  });
  if ($('#dealDialog').open) $('#dealDialog').close();
  render();
  showToast(value.state === 'parked'
    ? `${deal?.name || 'Work record'} parked`
    : `${deal?.name || 'Work record'} restored to active work`);
  return result;
}

function openForm({ eyebrow='Deal Room', title, submit='Save', body, onSubmit }) {
  const dialog = $('#formDialog');
  $('#dialogEyebrow').textContent = eyebrow;
  $('#dialogTitle').textContent = title;
  $('#dialogSubmit').textContent = submit;
  $('#dialogBody').innerHTML = body;
  $('#formError').hidden = true;
  $('#dialogForm').onsubmit = async (event) => {
    event.preventDefault();
    const button = $('#dialogSubmit');
    button.disabled = true;
    $('#formError').hidden = true;
    try {
      await onSubmit(new FormData(event.currentTarget));
      dialog.close();
    } catch (error) {
      const detail = error.payload?.hint || error.payload?.error || error.message;
      $('#formError').textContent = detail;
      $('#formError').hidden = false;
    } finally { button.disabled = false; }
  };
  // One dialog element serves every form, so a form raised FROM a form — the
  // conflict chooser opened while the park form is still up — is a content
  // swap, not a second opening. showModal() on an already-open dialog throws,
  // and that exception used to land on the error line the submit handler had
  // just cleared: the person got the conflict chooser with a browser message
  // above it. The body and the submit handler are already replaced above; this
  // only has to make the dialog visible when it is not.
  if (!dialog.open) dialog.showModal();
  setTimeout(() => $('input,textarea,select', dialog)?.focus(), 0);
}

function nextStepForm(dealId) {
  const deal = state.deals.get(dealId);
  // Track the content of the LAST submit attempt (not just "the form is
  // open"). A retry of an attempt that failed reuses that same attempt's
  // key, so the server's own idempotency_key replay (mcp-server/src/tools.js
  // withEnvelope) applies and a retry can never turn into a second write.
  // But if the user edits the text or date before resubmitting, that is a
  // genuinely different attempt — reusing the old key would make the server
  // treat the EDITED content as a replay of the stale one (key_reuse) or,
  // worse, silently keep the old content. A fresh key is minted only when
  // what's being submitted differs from what was last attempted.
  let lastAttempt = null; // { text, next_date, idempotencyKey }
  openForm({ title:`Next step — ${deal.name}`, submit:'Set next step', body:`
    <div class="field"><label for="stepText">What happens next?</label><textarea id="stepText" name="text" required>${esc(deal.next_step || '')}</textarea><small>This becomes a real next action in today’s triage; the prior step stays in history.</small></div>
    <div class="field"><label for="stepDate">When?</label><input id="stepDate" name="next_date" type="date" value="${esc(deal.next_date || '')}"></div>`,
    onSubmit:async (data) => {
      const text = String(data.get('text') || '').trim();
      const next_date = data.get('next_date') || null;
      const identicalRetry = lastAttempt && lastAttempt.text === text && lastAttempt.next_date === next_date;
      const idempotencyKey = identicalRetry ? lastAttempt.idempotencyKey : uuidv4();
      lastAttempt = { text, next_date, idempotencyKey };
      // Same runCommand the Doc panel's `/set_next_step` runs (commands.js),
      // and the SAME buildDealContext() the Doc's context comes from — one
      // code path and one context builder, so this form and Doc cannot drift
      // into different receipts, or different next_date handling, for the
      // identical write. The form always passes an EXPLICIT next_date (the
      // input is pre-filled with the deal's current value, so "no edit"
      // still resends it); the Doc composer never overrides it and so
      // preserves whatever buildDealContext defaults to — the deal's current
      // date — instead of clearing it.
      const context = buildDealContext(deal, { nextDate: next_date });
      const receipt = await runCommand(state.client, 'set_next_step', text, context, { idempotencyKey });
      if (receipt.status !== 'ok') throw new Error(receipt.detail?.reason || 'This next step was not set.');
      const current = confirmLocalWrite(dealId, { next_step:text, next_date });
      showToast(`Next step set on ${current?.name || deal.name}`); renderBoardOnly();
    } });
}

function parkDealForm(dealId) {
  const deal = state.deals.get(dealId);
  openForm({ eyebrow:'Active work', title:`Park — ${deal.name}`, submit:'Park record', body:`
    <p class="form-guidance">Parking removes this record from active counts, focus lists, and weekly agendas. Its Salesforce link, phase, history, and participants stay intact.</p>
    <div class="field"><label for="parkingReason">Why is this not active work?</label><select id="parkingReason" name="reason" required>
      <option value="prospect_never_active">Prospect never became active work</option>
      <option value="client_paused">Client paused activity</option>
      <option value="other">Other / not active right now</option>
    </select></div>
    <div class="field"><label for="parkingNote">Context (optional)</label><textarea id="parkingNote" name="note" maxlength="500" placeholder="What would help when this record becomes active again?"></textarea></div>`,
    onSubmit:async (data) => {
      const result = await patchOperatingState(dealId, {
        state:'parked', reason:String(data.get('reason')), note:String(data.get('note') || '').trim() || null,
      }, { surface:'inline' });
      // Only a parked record closes this form. An answer that did not park it —
      // refused, or never confirmed — keeps the reason and the note the partner
      // typed, and says why on the form's own error line instead of in a toast
      // over a dialog that has already thrown the draft away.
      if (result.status !== 'ok') throw new Error(result.message || 'This record was not parked.');
    } });
}

function marketAgentForm(dealId) {
  const deal = state.deals.get(dealId);
  openForm({ title:`Market assignment — ${deal.name}`, submit:'Save assignment', body:`
    <div class="field"><label for="agentName">Assigned local agent</label><input id="agentName" name="agent_name" required value="${esc(deal.market_agent || '')}" placeholder="Agent’s full name"></div>
    <div class="field"><label for="agentMarket">Market</label><input id="agentMarket" name="market" value="${esc(deal.market || '')}" placeholder="City, state"></div>`,
    onSubmit:async (data) => {
      await state.client.setMarketAgent({ deal:dealId, agent_name:data.get('agent_name'), market:data.get('market') || null,
        source:'partner stated in Deal Room', idempotency_key:uuidv4() });
      const current = confirmLocalWrite(dealId, { market_agent:data.get('agent_name'),
        ...(data.get('market') ? { market:data.get('market') } : {}) });
      renderBoardOnly(); showToast(`Market agent saved on ${current?.name || deal.name}`);
    } });
}

function addTeamDealForm() {
  openForm({ title:'Add work record', submit:'Create work record', body:`
    <div class="field"><label for="clientRef">Existing client</label><input id="clientRef" name="client" required placeholder="C-127 or exact client name"><small>A work record always belongs to a client. This prevents free-floating or duplicate records.</small></div>
    <div class="field"><label for="dealName">Record name</label><input id="dealName" name="name" required></div>
    <div class="field-row"><div class="field"><label for="dealType">Type</label><select id="dealType" name="deal_type"><option value="startup">Startup</option><option value="relocation">Relocation</option><option value="additional_office">Additional office</option><option value="renewal">Renewal</option><option value="expansion">Expansion</option><option value="purchase">Purchase</option><option value="other">Other</option></select></div>
    <div class="field"><label for="dealPhase">Phase</label><select id="dealPhase" name="phase">${phaseOptions('On Deck')}</select></div></div>
    <div class="field-row"><div class="field"><label for="dealMarket">Market</label><input id="dealMarket" name="market"></div><div class="field"><label for="dealSegment">Healthcare vertical</label><input id="dealSegment" name="segment" placeholder="Dental, Vet, DPC…"></div></div>`,
    onSubmit:async (data) => {
      const args = Object.fromEntries(data.entries());
      await state.client.createDeal({ ...args, lane:'territory', idempotency_key:uuidv4() });
      await loadHome(); showToast('Work record created in the Team Book');
    } });
}

function addAccountForm() {
  openForm({ eyebrow:'National accounts', title:'Add national account', submit:'Create account', body:`
    <div class="field"><label for="accountName">Brand / organization</label><input id="accountName" name="name" required><small>Creates one parent account. Market transactions will live under their own sub-clients.</small></div>
    <div class="field-row"><div class="field"><label for="accountOwner">Account owner</label><select id="accountOwner" name="owner"><option value="${esc(state.selfActor)}">${esc(actorName(state.selfActor))}</option><option value="${state.selfActor === 'joe' ? 'dell' : 'joe'}">${esc(actorName(state.selfActor === 'joe' ? 'dell' : 'joe'))}</option></select></div>
    <div class="field"><label for="accountVertical">Healthcare vertical</label><input id="accountVertical" name="vertical"></div></div>`,
    onSubmit:async (data) => { await state.client.createNationalAccount({ ...Object.fromEntries(data.entries()), idempotency_key:uuidv4() }); await loadHome(); showToast('National account created'); } });
}

function addMarketDealForm() {
  const selected = account();
  openForm({ eyebrow:selected.account_name, title:'Add market transaction', submit:'Create market deal', body:`
    <div class="field"><label for="marketClient">Franchisee / local client</label><input id="marketClient" name="client_name" required><small>Reuses the exact sub-client if it exists; otherwise creates one under ${esc(selected.account_name)}.</small></div>
    <div class="field"><label for="marketDealName">Deal name</label><input id="marketDealName" name="deal_name" required></div>
    <div class="field-row"><div class="field"><label for="marketCity">Market</label><input id="marketCity" name="market" required placeholder="City"></div><div class="field"><label for="marketState">State</label><input id="marketState" name="state" maxlength="2"></div></div>
    <div class="field-row"><div class="field"><label for="marketAgent">Assigned agent</label><input id="marketAgent" name="agent_name" placeholder="Leave blank if unknown"></div><div class="field"><label for="marketSegment">Healthcare vertical</label><input id="marketSegment" name="segment"></div></div>
    <input type="hidden" name="deal_type" value="startup"><input type="hidden" name="phase" value="pending">`,
    onSubmit:async (data) => { await state.client.createNationalMarketDeal({ account_client_id:selected.account_client_id,
      ...Object.fromEntries(data.entries()), idempotency_key:uuidv4() }); await loadHome(); showToast(`Market deal added to ${selected.account_name}`); } });
}

function accountOwnerForm() {
  const selected = account();
  openForm({ eyebrow:selected.account_name, title:'Change account owner', submit:'Save owner', body:`
    <div class="field"><label for="newAccountOwner">Accountable partner</label><select id="newAccountOwner" name="owner"><option value="joe"${selected.account_owner === 'joe' ? ' selected' : ''}>Joe</option><option value="dell"${selected.account_owner === 'dell' ? ' selected' : ''}>Dell</option></select><small>This changes portfolio accountability only. Individual market-deal owners stay untouched.</small></div>`,
    onSubmit:async (data) => { await state.client.setNationalAccountOwner({ account_client_id:selected.account_client_id,
      owner:data.get('owner'), idempotency_key:uuidv4() }); await loadHome(); showToast(`${selected.account_name} owner updated`); } });
}

function showConflict(conflict) {
  const display = (value) => value && typeof value === 'object'
    ? value.state === 'parked' ? `Parked — ${parkingReasonLabel(value.reason)}` : 'Active work'
    : value ?? '(empty)';
  openForm({ eyebrow:'Two edits crossed', title:`Choose the value for ${conflict.field}`, submit:'Keep selected value', body:`
    <div class="field"><label><input type="radio" name="winner" value="a" checked> ${esc(actorName(conflict.a.actor))}: ${esc(display(conflict.a.value))}</label></div>
    <div class="field"><label><input type="radio" name="winner" value="b"> ${esc(actorName(conflict.b.actor))}: ${esc(display(conflict.b.value))}</label></div>`,
    onSubmit:async (data) => { await state.client.resolveConflict({ conflict_id:conflict.conflict_id, winner:data.get('winner'), idempotency_key:uuidv4() }); await loadHome(); showToast('Conflict resolved with both values preserved in history'); } });
}

function detailRows(items, renderer, empty='Nothing captured yet.') {
  return items?.length ? items.map(renderer).join('') : `<div class="detail-row">${esc(empty)}</div>`;
}

async function openDeal(dealId) {
  // Set only AFTER getDeal succeeds: a failed read must never leave the Doc
  // panel believing a deal is open that the user never actually saw (it
  // would offer commands against a dialog that never showed).
  const detail = await state.client.getDeal(dealId);
  state.docContextDealId = dealId;
  // A Doc panel already open (pinned, or opened before any deal was) must
  // pick up the new "working on" deal immediately, not only the next time
  // Doc itself is opened.
  refreshDocContext();
  const deal = detail.deal;
  const parked = deal.operating_state === 'parked';
  const html = `<header><div><p class="eyebrow">${esc(deal.account_name || deal.client_name || 'Work record')}</p><h2>${esc(deal.name)}</h2><p class="subhead">${parked ? `${esc(parkingReasonLabel(deal.parking_reason))} · ` : ''}${esc(deal.phase)} · ${esc(deal.market || 'Market not captured')}</p></div><div class="detail-header-actions"><button type="button" class="park-button" data-operating-state="${parked ? 'active' : 'parked'}" data-deal="${esc(deal.id)}">${parked ? 'Restore to active' : 'Park'}</button><button type="button" class="icon-button" data-close-deal aria-label="Close details">×</button></div></header>
    <div class="deal-content">${parked ? `<div class="parking-banner"><b>${esc(parkingReasonLabel(deal.parking_reason))}</b>${deal.parking_note ? `<span>${esc(deal.parking_note)}</span>` : ''}<small>This record is outside active counts and weekly agendas.</small></div>` : ''}<div class="deal-summary">
      <div class="detail-card"><label>Next step</label><p>${esc(deal.next_step || 'Not set')}</p></div>
      <div class="detail-card"><label>Next date</label><p>${esc(dateLabel(deal.next_date))}</p></div>
      <div class="detail-card"><label>${deal.workspace_kind === 'national_account' ? 'Market agent' : 'Owner'}</label><p>${esc(deal.market_agent || actorName(deal.owner))}</p></div>
      <div class="detail-card"><label>Last touch</label><p>${esc(relative(deal.last_touch))}</p></div></div>
      <section class="detail-section"><h3>Open next actions</h3><div class="detail-list">${detailRows((detail.next_actions || []).filter((a) => a.status === 'open'), (a) => `<div class="detail-row"><b>${esc(a.description)}</b><small>${esc(actorName(a.owner))} · ${esc(dateLabel(a.due_on))}</small></div>`)}</div></section>
      <section class="detail-section"><h3>Critical dates</h3><div class="detail-list">${detailRows(detail.critical_dates, (d) => `<div class="detail-row"><b>${esc(d.label || d.kind)}</b><small>${esc(dateLabel(d.date || d.due_on))} · source: ${esc(d.source || 'not captured')}</small></div>`)}</div></section>
      <section class="detail-section"><h3>Premises</h3><div class="detail-list">${detailRows(detail.premises, (p) => `<div class="detail-row"><b>${esc(p.label)}</b><small>${esc([p.address,p.suite,p.city,p.state].filter(Boolean).join(' · '))}${p.area_amount ? ` · ${esc(p.area_amount)} ${esc(p.area_basis || 'SF')}` : ''}</small></div>`)}</div></section>
      <section class="detail-section"><h3>Negotiation rounds</h3><div class="detail-list">${detailRows(detail.negotiation_rounds, (n) => `<div class="detail-row"><b>Round ${esc(n.round_no)} · ${esc(n.side)}</b><small>${esc(n.rate_amount ? `${n.rate_amount} ${n.rate_basis || ''}` : 'Rate not captured')} · ${esc(n.term_months ? `${n.term_months} months` : 'Term not captured')}</small></div>`)}</div></section>
      <section class="detail-section"><h3>Participants</h3><div class="detail-list">${detailRows(detail.participants, (p) => `<div class="detail-row"><b>${esc(p.name)}</b><small>${esc(String(p.role).replaceAll('_',' '))}</small></div>`)}</div></section>
      <section class="detail-section"><h3>Recent activity</h3><div class="detail-list">${detailRows(detail.activities, (a) => `<div class="detail-row"><b>${esc(a.summary)}</b><small>${esc(actorName(a.actor))} · ${esc(relative(a.occurred_at))} · ${esc(a.kind)}</small></div>`)}</div></section>
      <section class="detail-section"><h3>Notes and prior next steps</h3><div class="detail-list">${detailRows(detail.thread, (n) => `<div class="detail-row">${esc(n.text)}<small>${esc(actorName(n.actor))} · ${esc(n.kind === 'archived_step' ? 'prior next step' : 'note')}</small></div>`)}</div></section>
      <section class="detail-section"><h3>Documents</h3><div class="detail-list">${detailRows(detail.documents, (d) => `<div class="detail-row"><b>${esc(String(d.sent_status).replaceAll('_',' '))}</b><small>Prepared ${esc(relative(d.prepared_at))} · lint ${d.lint_passed ? 'passed' : 'not confirmed'} · leak check ${d.leak_check_passed ? 'passed' : 'not confirmed'}</small></div>`)}</div></section>
      <section class="detail-section"><h3>Change history</h3><div class="detail-list">${detailRows(detail.history, (h) => `<div class="detail-row">${esc(h.summary)}<small>${esc(actorName(h.actor))} · ${esc(relative(h.recorded_at))}</small></div>`)}</div></section>
    </div>`;
  $('#dealDetail').innerHTML = html;
  $('#dealDialog').showModal();
}

function agendaDeals() {
  let deals = [...state.deals.values()].filter((deal) => (deal.operating_state || 'active') === 'active');
  if (state.workspace === 'team') deals = deals.filter((deal) => deal.workspace_kind === 'team');
  else if (state.accountId) deals = deals.filter((deal) => deal.account_client_id === state.accountId);
  else deals = [];
  return deals.sort((a,b) => priority(b) - priority(a) || a.name.localeCompare(b.name));
}

async function startAgenda() {
  const deals = agendaDeals();
  if (!deals.length) return showToast('No active work in this agenda');
  const result = await state.client.startReview({ workspace_kind:state.workspace,
    ...(state.accountId ? { account_client_id:state.accountId } : {}), idempotency_key:uuidv4() });
  state.review = { sessionId:result.session_id, deals, index:0, reviewed:0, skipped:0 };
  $('#agendaPanel').hidden = false;
  renderAgenda();
}

function renderAgenda() {
  const review = state.review;
  if (!review) return;
  // The agenda captured its set and its order when it started, and both are
  // kept: a record does not join or leave the list mid-review. The VALUES are
  // resolved now, because a snapshot has replaced every row object since —
  // possibly several times, on the periodic read alone — and the captured copy
  // would show the partner the phase and next step as they were at the start.
  const captured = review.deals[review.index];
  const deal = resolveCurrentRow(captured, state.deals);
  $('#agendaTitle').textContent = state.workspace === 'team' ? 'Team Book' : account()?.account_name || 'National account';
  $('#agendaProgress').textContent = `${Math.min(review.index + 1,review.deals.length)} of ${review.deals.length}`;
  $('#agendaMeter').max = review.deals.length;
  $('#agendaMeter').value = review.index;
  if (!deal) return finishAgenda();
  $('#agendaCard').innerHTML = `<article class="agenda-deal"><p class="eyebrow">${esc(deal.market || deal.client_name || '')}</p><h3>${esc(deal.name)}</h3>
    <span class="agenda-reason">${esc(reasonFor(deal))}</span>
    <div class="agenda-fact"><label>Phase</label><p>${esc(deal.phase)}</p></div>
    <div class="agenda-fact"><label>Next step</label><p>${esc(deal.next_step || 'Not set')}</p></div>
    <div class="agenda-fact"><label>Next date</label><p>${esc(dateLabel(deal.next_date))}</p></div>
    ${deal.workspace_kind === 'national_account' ? `<div class="agenda-fact"><label>Assigned market agent</label><p>${esc(deal.market_agent || 'Unassigned')}</p></div>` : ''}
    <button type="button" class="secondary" data-open-deal="${esc(deal.id)}">Open full record</button>
    <button type="button" class="secondary" data-agenda-step="${esc(deal.id)}">Set next step</button></article>`;
}

async function advanceAgenda(disposition) {
  const review = state.review;
  const deal = review?.deals[review.index];
  if (!review || !deal) return;
  await state.client.reviewDeal({ session_id:review.sessionId, deal:deal.id, disposition, idempotency_key:uuidv4() });
  review[disposition === 'reviewed' ? 'reviewed' : 'skipped'] += 1;
  review.index += 1;
  renderAgenda();
}

async function finishAgenda(status = 'completed') {
  if (!state.review) return;
  const review = state.review;
  const result = await state.client.endReview({ session_id:review.sessionId, status, idempotency_key:uuidv4() });
  state.review = null;
  $('#agendaPanel').hidden = true;
  await loadHome();
  showToast(status === 'completed' ? `Agenda finished · ${result.reviewed ?? review.reviewed} records reviewed` : 'Agenda closed without changing the review clock');
}

function wireEvents() {
  // Whatever the dialog closes through — the × button, Escape, the browser's
  // own light-dismiss — this fires either way, so it is the one place that
  // clears the Doc panel's "working on" context rather than every call site
  // that can close #dealDialog remembering to do it.
  $('#dealDialog').addEventListener('close', () => { state.docContextDealId = null; refreshDocContext(); });
  document.addEventListener('click', async (event) => {
    const workspace = event.target.closest('[data-workspace]');
    if (workspace) { state.workspace = workspace.dataset.workspace; state.accountId = null; state.filter = 'active'; state.deepLinkMine = false; state.query = ''; $('#search').value = ''; render(); return; }
    const accountButton = event.target.closest('[data-account]');
    if (accountButton) { state.workspace = 'national_account'; state.accountId = accountButton.dataset.account; render(); return; }
    const retryWrite = event.target.closest('[data-retry-write]');
    if (retryWrite) { await retryCellWrite(retryWrite.dataset.retryWrite, retryWrite); return; }
    const operating = event.target.closest('[data-operating-state]');
    if (operating) {
      const dealId = operating.dataset.deal;
      // Restore from the open deal dialog is answered inside that dialog: the
      // modal's backdrop dims and covers a toast, and the row's Retry with it.
      if (operating.dataset.operatingState === 'active') await patchOperatingState(dealId, { state:'active' }, { surface:writeSurfaceFor(operating) });
      else { if ($('#dealDialog').open) $('#dealDialog').close(); parkDealForm(dealId); }
      return;
    }
    const open = event.target.closest('[data-open-deal]'); if (open) { await openDeal(open.dataset.openDeal); return; }
    const attention = event.target.closest('[data-attention]'); if (attention) { const deal=state.deals.get(attention.dataset.attention); await patchField(deal.id,'attention',!deal.attention); return; }
    const step = event.target.closest('[data-next-step],[data-agenda-step]'); if (step) { nextStepForm(step.dataset.nextStep || step.dataset.agendaStep); return; }
    const agent = event.target.closest('[data-market-agent]'); if (agent) { marketAgentForm(agent.dataset.marketAgent); return; }
    const filter = event.target.closest('[data-filter]'); if (filter) { state.filter=filter.dataset.filter; state.deepLinkMine = false; $$('.filter').forEach((b)=>b.classList.toggle('on',b===filter)); renderBoardOnly(); return; }
    if (event.target.closest('[data-close-deal]')) { $('#dealDialog').close(); return; }
    const undoButton = event.target.closest('[data-undo]'); if (undoButton) { await runUndo(undoButton.dataset.undo, undoButton); return; }
    const confirm = event.target.closest('[data-confirm]'); if (confirm) { const chip=confirm.closest('[data-proposal]'); const yes=confirm.dataset.confirm==='yes'; await state.client.resolveConfirm({proposal_id:chip.dataset.proposal,accept:yes,idempotency_key:uuidv4()}); state.confirms=state.confirms.filter((p)=>p.id!==chip.dataset.proposal); renderConfirms(); if(yes)await loadHome(); showToast(yes?'Suggestion confirmed':'Suggestion skipped'); return; }
    if (event.target.closest('[data-dialog-cancel]')) { $('#formDialog').close(); return; }
    // Deliberately no recorder branch below this line. This delegated listener
    // sees every click in the page, so a branch here is the one thing that could
    // turn a synthetic click into a recording; the Calls entrypoint is wired to
    // its own control instead, and it opens a dialog and nothing more.
  });

  document.addEventListener('change', async (event) => {
    if (event.target.matches('[data-phase]')) await patchField(event.target.dataset.phase,'phase',event.target.value);
    if (event.target.matches('[data-owner]')) await patchField(event.target.dataset.owner,'owner',event.target.value || null);
  });

  $('#search').addEventListener('input', (event) => { state.query=event.target.value.trim(); render(); });
  $('#accountBack').onclick = () => { state.accountId=null; render(); };
  const openAddForm = () => state.workspace === 'team' ? addTeamDealForm() : state.accountId ? addMarketDealForm() : addAccountForm();
  $('#stickyAddButton').onclick = openAddForm;
  $('#receiptsJump').onclick = goToReceipts;
  $('#ownerButton').onclick = accountOwnerForm;
  $('#agendaButton').onclick = startAgenda;
  $('#agendaReviewed').onclick = () => advanceAgenda('reviewed');
  $('#agendaSkip').onclick = () => advanceAgenda('skipped');
  $('#agendaEnd').onclick = () => finishAgenda('completed');
  $('#agendaClose').onclick = () => finishAgenda('abandoned');
  $('#themeButton').onclick = () => { document.body.classList.toggle('night'); localStorage.setItem('dealroom-theme',document.body.classList.contains('night')?'night':'light'); };
  $('#colorAssistButton').onclick = () => {
    const enabled = !document.body.classList.contains('color-assist');
    document.body.classList.toggle('color-assist', enabled);
    const button = $('#colorAssistButton');
    button.setAttribute('aria-pressed', String(enabled));
    button.setAttribute('aria-label', `${enabled ? 'Turn off' : 'Turn on'} color-blind-friendly view`);
    localStorage.setItem('dealroom-color-assist', enabled ? 'on' : 'off');
    showToast(enabled ? 'Color-friendly view on · patterns and labels supplement color' : 'Color-friendly view off');
  };
  // Coming back is not the same as being current: BOTH reads start again from
  // scratch, and `force` supersedes whatever was left hanging when the
  // connection went away rather than queueing behind it. Without the forced
  // poll, a feed read that never answers would leave every later tick skipping
  // as "already polling" for the rest of the session.
  window.addEventListener('online', () => {
    $('#offlineBanner').hidden = true;
    state.boardSync.setOnline(true);
    state.boardSync.refreshBoard({ reason:'reconnect', force:true });
    pollOnce(false, { force:true });
  });
  window.addEventListener('offline', () => {
    $('#offlineBanner').hidden = false;
    state.boardSync.setOnline(false);
  });
  document.addEventListener('keydown', (event) => { if (event.key==='/' && !event.target.matches('input,textarea,select')) { event.preventDefault(); $('#search').focus(); } });
}

async function boot() {
  // FIRST, ahead of every read that can fail. The whole of Calls in this
  // release is a control that explains its own absence, plus a sweep for any
  // recorder control a cached page is still carrying — and the sweep has to run
  // even when the rest of boot does not. If sign-in or the network fails after
  // this line, the failure is a shell that offers nothing; if it ran later, the
  // same failure would leave a stale "Start weekly deal call" on screen.
  installCallsBoundary();
  if (localStorage.getItem('dealroom-theme') === 'night') document.body.classList.add('night');
  if (localStorage.getItem('dealroom-color-assist') === 'on') {
    document.body.classList.add('color-assist');
    $('#colorAssistButton').setAttribute('aria-pressed', 'true');
    $('#colorAssistButton').setAttribute('aria-label', 'Turn off color-blind-friendly view');
  }
  const bootConfig = resolveDealroomBoot(location);
  const params = new URLSearchParams(location.search);
  if (params.get('workspace') === 'team') state.workspace = 'team';
  if (params.get('filter') === 'flagged') { state.filter = 'flagged'; state.deepLinkMine = params.get('owner') === 'me'; }
  const identity = deploymentIdentity(bootConfig.mode);
  state.mode = bootConfig.mode;
  const badge = $('#deploymentBadge');
  badge.textContent = identity.label;
  badge.dataset.mode = identity.mode;
  badge.title = identity.detail;
  badge.setAttribute('aria-label', identity.detail);
  state.client = await createClient(bootConfig.mode, bootConfig.options);
  // The Doc panel calls back into THIS module's own client/context instead of
  // creating a second client, so a command it runs and a command the deal
  // dialog runs are the same write against the same session (V5-J101
  // checkable_done: "the same command issued from the UI and from the Doc
  // yields an equivalent receipt").
  registerDocPanelSource({
    getClient: () => state.client,
    // The SAME buildDealContext() nextStepForm's onSubmit calls, with no
    // override — so a Doc command that changes the next step defaults to the
    // deal's CURRENT date and preserves it, exactly like the form's
    // pre-filled date input does, instead of the two diverging.
    getContext: () => buildDealContext(state.deals.get(state.docContextDealId)),
  });
  state.boardSync = createBoardSync({
    readBoard: () => state.client.getBoard({ workspace:'all' }),
    readChanges: (cursor) => state.client.getChanges(cursor),
    // Only a snapshot that is still current reaches this callback, and it
    // already carries any value a confirmed local write is holding.
    applyBoard: (board) => { applyBoardSnapshot(board); if (!userIsEditing()) renderPreservingFocus(); },
    onStatus: setSync,
  });
  wireEvents();
  await loadHome();
  // A tick that arrives while the last poll is still open is dropped by the
  // coordinator rather than run alongside it, so a slow answer cannot land
  // after a newer one.
  state.pollTimer = setInterval(() => {
    pollOnce().catch((error) => console.error('Deal Room poll failed to render', error));
  }, POLL_MS);
  // The backstop for what the feed cannot promise. A change whose commit landed
  // out of order with its recorded time can slip past the cursor, and a board
  // that only re-reads on news would then hold that wrong value for as long as
  // the page stayed open. This asks anyway. It coalesces with the feed-driven
  // and post-write refreshes through the same single-read queue, so it adds one
  // read per interval at most and none while another is already running.
  state.boardRefreshTimer = setInterval(() => {
    state.boardSync.requestRefresh('periodic');
  }, BOARD_REFRESH_MS);
  // Two timers, both about the board. There is deliberately no third one for a
  // recorder clock: this release has no recording to time and nothing local to
  // ask about one.
  if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('/sw.js').catch(()=>{});
}

/**
 * The shell starts itself only when it is actually in its own page.
 *
 * The condition is the board's own root element, not a flag or a setting: a
 * harness that imports this module to exercise a function gets the functions and
 * none of the timers, reads or listeners, and there is no switch that changes
 * that in either direction. `#rows` is asserted to exist in index.html by the
 * release tests, so the sentinel cannot drift away from the markup it names.
 */
if (typeof document !== 'undefined' && document.getElementById('rows')) {
  boot().catch((error) => {
    console.error(error);
    document.body.insertAdjacentHTML('afterbegin', `<div class="offline">Deal Room could not start: ${esc(error.message)}</div>`);
  });
}
