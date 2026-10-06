/**
 * Doc — persistent, pinnable command panel (V5-J101).
 *
 * The app persona is Dr. CRE ("Doc" is only the spoken nickname — CLAUDE.md).
 * The visible label stays "Doc"; the accessible name (what a screen reader
 * announces) is "Dr. CRE" on both the toggle button and the panel itself.
 *
 * Self-mounting: a page adds `<script type="module" src="/js/doc-panel.js">`
 * and gets the toggle button plus panel with no markup changes of its own.
 * A host page that HAS a client and a current record calls
 * `registerDocPanelSource({ getClient, getContext })` once (see js/app.js);
 * until it does, the panel still opens/closes/keyboard-operates correctly
 * and reports every command as {status:'unavailable'} — an honest state, not
 * a broken one (see architecture_or_design guidance for V5-J101: never ship
 * mocks as live behaviour).
 *
 * Modality mirrors the record panel exactly (workspace-business.js): docked
 * and non-modal on a desktop, a real focus-trapped dialog with the rest of
 * the page made inert at phone width. docPanelModality/escapeShouldClose live
 * in doc-panel-model.js so that rule is unit-testable without a DOM; the
 * tab-stop decision reuses workspace-business-model.js's own panelTabTarget
 * rather than a second, drifting implementation of the same rule.
 *
 * ROUND 2 fixes (independent review of PR #1259 at e9395b51):
 *  - index.html's #dealDialog/#formDialog are native <dialog> elements shown
 *    with showModal(), which makes everything OUTSIDE the dialog's own
 *    subtree inert — including a Doc panel appended to document.body. A
 *    MutationObserver watches every <dialog>'s `open` attribute and
 *    reparents the toggle+panel into whichever one is currently open (there
 *    is at most one at a time in this app), and back to document.body when
 *    none is, so Doc stays reachable while a deal or form dialog is up.
 *  - The focus trap previously built its stop list from the panel's own
 *    focusable elements only, so Shift+Tab from the heading (a stop-less
 *    tabindex="-1" element) fell through to the browser's native previous
 *    element — the toggle button, which the inert sweep deliberately
 *    excluded so it stayed clickable. The toggle is no longer excluded from
 *    that sweep (the in-panel Close button is sufficient while modal) and
 *    panelTabTarget now owns the heading case exactly as it does for the
 *    record panel.
 *  - applyModality swept ALL of document.body's other children and forced
 *    each one's `inert`/`aria-hidden` on close, which could undo a `true`
 *    another panel (e.g. the record panel on business.html) had
 *    independently set on the SAME element for its own still-open modal.
 *    Each region's prior inert/aria-hidden state is now snapshotted before
 *    Doc changes it, and restored exactly — not forced false — when Doc
 *    stops covering it.
 *
 * ROUND 3 fixes (independent review at afc85607 — this round replaces the
 * round-2 snapshot/restore above with something that tolerates a THIRD
 * PARTY changing a shared region's inert state while Doc still holds a
 * snapshot of it, which the round-2 version did not):
 *  - Doc and the record panel (workspace-business.js) now share ONE
 *    reference-counted inert registry (inert-registry.js) instead of each
 *    keeping its own snapshot/restore. A snapshot/restore only remembers
 *    "what this region looked like before I touched it" — it has no idea
 *    whether some OTHER, uncoordinated panel also currently needs the same
 *    region inert, so whichever panel claimed or released SECOND could stomp
 *    the other's still-active claim, in EITHER order: the reported defect
 *    was the record panel releasing first and Doc's stale snapshot
 *    re-claiming on top of it ("Escape kills the page" at 375px on
 *    business.html); the untested mirror case was Doc claiming a region
 *    first and later releasing it after the record panel had, in the
 *    meantime, also independently claimed it. Reference counting removes the
 *    ordering dependency: a region stays inert while at least one owner
 *    claims it, and is restored to its true pre-claim baseline only once the
 *    LAST owner releases it. `claimedByMe` here is just Doc's own
 *    bookkeeping of which regions it currently holds a claim on, so a host
 *    switch (relocate()) can release exactly those before moving, fixing
 *    #dealDetail staying inert after a pinned Doc outlived its dialog.
 *  - The Doc toggle is now inert (unreachable) whenever another modal is
 *    covering the page and Doc itself is closed, matching a true modal's
 *    "nothing outside it is reachable" semantics on business.html, where the
 *    record panel does not otherwise know the toggle exists to inert it.
 *  - renderContext() is now re-invoked whenever a host page's notion of the
 *    open deal changes (see exported refreshDocContext), not only on Doc's
 *    own open() — a Doc opened before any deal, or pinned across a deal
 *    dialog closing, no longer shows a stale "Working on" line.
 *  - Retry no longer reads a single shared mutable "last attempt" slot on
 *    state, or re-reads source.getContext() at click time. Each failed line's retry
 *    closure captures its own command/text/context/idempotencyKey at the
 *    moment IT failed; clicking an older Retry after a newer line has
 *    already succeeded can never throw and can never write to whatever deal
 *    happens to be open now instead of the one it actually targets.
 */

import { docPanelModality, escapeShouldClose, parseDocInput, formatReceiptLine, retryContextDriftNote } from './doc-panel-model.js';
import { panelTabTarget } from './workspace-business-model.js';
import { runCommand, listCommands } from './commands.js';
import { claimInert, releaseInert, isClaimedByOther } from './inert-registry.js';

const OWNER = 'doc';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
const PIN_KEY = 'dealroom-doc-pinned';

let source = { getClient: () => null, getContext: () => ({}) };
let currentRenderContext = null;

/**
 * Called by a host page whenever ITS notion of "the open deal" changes
 * (opening a deal, closing its dialog, switching records) so an already-open
 * Doc panel's "Working on" line stays live instead of only updating the next
 * time Doc itself is opened. Safe to call when Doc has not mounted yet, or
 * is currently closed — it just re-renders the (possibly hidden) label.
 */
export function refreshDocContext() {
  currentRenderContext?.();
}

/**
 * Called by a host page (app.js, workspace-business.js, …) once its client
 * and current-record context exist. Safe to call more than once; the last
 * registration wins, which matters when a page tears down and re-mounts.
 * @param {{getClient: () => any, getContext: () => {dealId?:string|null, nextDate?:string|null, label?:string}}} next
 */
export function registerDocPanelSource(next) {
  source = { getClient: next.getClient || source.getClient, getContext: next.getContext || source.getContext };
}

function readPinned() {
  try { return localStorage.getItem(PIN_KEY) === '1'; } catch { return false; }
}
function writePinned(value) {
  try { localStorage.setItem(PIN_KEY, value ? '1' : '0'); } catch { /* private mode / blocked storage: pin just won't persist */ }
}

function phoneWidthMatch() {
  return typeof window.matchMedia === 'function' ? window.matchMedia('(max-width: 767px)') : null;
}

function focusWithoutScrolling(element) {
  element?.focus?.({ preventScroll: true });
}

function mount() {
  if (document.getElementById('docPanel')) return; // already mounted on this page

  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.id = 'docPanelToggle';
  toggle.className = 'doc-toggle';
  toggle.setAttribute('aria-haspopup', 'dialog');
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', 'docPanel');
  // Accessible name is the persona, "Dr. CRE"; the visible label stays the
  // nickname, "Doc" (CLAUDE.md naming rule).
  toggle.setAttribute('aria-label', 'Dr. CRE');
  toggle.innerHTML = '<span aria-hidden="true">◐</span><span class="doc-toggle-label" aria-hidden="true">Doc</span>';

  const panel = document.createElement('aside');
  panel.id = 'docPanel';
  panel.className = 'doc-panel';
  panel.hidden = true;
  // aria-labelledby concatenates both nodes: accessible name becomes
  // "Dr. CRE Doc" — it CONTAINS the required persona name while the visible
  // heading stays the nickname alone.
  panel.setAttribute('aria-labelledby', 'docPanelEyebrow docPanelTitle');
  panel.innerHTML = `
    <div class="doc-panel-head">
      <div><p class="eyebrow" id="docPanelEyebrow">Dr. CRE</p><h2 id="docPanelTitle" tabindex="-1">Doc</h2></div>
      <div class="doc-panel-head-actions">
        <button type="button" id="docPanelPin" class="doc-icon-button" aria-pressed="false">Pin</button>
        <button type="button" id="docPanelClose" class="doc-icon-button" aria-label="Close Dr. CRE">Close</button>
      </div>
    </div>
    <p class="doc-panel-context" id="docPanelContext" aria-live="polite">Working on: nothing open yet.</p>
    <div class="doc-panel-log" id="docPanelLog" role="log" aria-live="polite" aria-label="Doc activity"></div>
    <form id="docPanelForm" class="doc-panel-form">
      <label for="docPanelInput" class="visually-hidden">Tell Doc what to do</label>
      <input id="docPanelInput" name="input" type="text" autocomplete="off" spellcheck="false"
             placeholder="Type a note, or /set_next_step …">
      <button type="submit" class="action primary-action">Send</button>
    </form>
    <p class="doc-panel-hint" id="docPanelHint">Same commands as the record panel — this runs the identical typed command, not a shortcut around it.</p>
  `;

  document.body.append(toggle, panel);

  const state = { open: false, pinned: readPinned() };
  const dom = {
    toggle, panel,
    title: panel.querySelector('#docPanelTitle'),
    context: panel.querySelector('#docPanelContext'),
    log: panel.querySelector('#docPanelLog'),
    form: panel.querySelector('#docPanelForm'),
    input: panel.querySelector('#docPanelInput'),
    pin: panel.querySelector('#docPanelPin'),
    close: panel.querySelector('#docPanelClose'),
  };

  const phoneQuery = phoneWidthMatch();
  const isModal = () => docPanelModality({ open: state.open, phoneWidth: Boolean(phoneQuery?.matches) }) === 'modal';

  // ------------------------------------------------------- top-layer host
  //
  // A native <dialog> shown with showModal() (index.html's #dealDialog and
  // #formDialog) makes everything outside ITS OWN subtree inert. The Doc
  // panel has to live inside whichever one is currently open to stay
  // reachable, and back in document.body the rest of the time.

  function openDialogHost() {
    const openDialog = document.querySelector('dialog[open]');
    return (openDialog && openDialog !== panel && !panel.contains(openDialog)) ? openDialog : document.body;
  }

  // ------------------------------------------------------- inert background
  //
  // Claim/release each region through the shared reference-counted registry
  // (inert-registry.js) rather than writing `inert`/`aria-hidden` directly —
  // see that file's own header for why a snapshot/restore approach is not
  // enough once TWO independently-modal panels (Doc, and the record panel on
  // business.html) can both reach the same region: whichever one claimed or
  // released SECOND could stomp the other's still-active claim, in EITHER
  // order. `claimedByMe` is local bookkeeping only — which regions Doc itself
  // currently holds a claim on — so a host switch knows what to release
  // before moving; it carries no state about the region itself (the registry
  // owns that).
  const claimedByMe = new Set();

  function otherRegions() {
    // The toggle is NOT excluded here (an earlier draft excluded it so it
    // stayed clickable while modal, which is exactly what let Shift+Tab from
    // the heading escape the trap onto it). The in-panel Close button is
    // sufficient while modal, matching the record panel's own pattern of a
    // fully-inert background with no exception.
    const host = toggle.parentNode || document.body;
    return [...host.children].filter((el) => el !== panel);
  }

  // Called right before Doc moves to a different host (a dialog opening or
  // closing under it). Everything Doc currently holds a claim on belongs to
  // the OLD host's subtree; if the claim were left in place across the move,
  // applyModality would only ever look at the NEW host's children again and
  // those old regions (e.g. #dealDetail behind a dialog a pinned Doc just
  // outlived) would stay claimed — and therefore inert — forever, with no
  // code path left that ever revisits them to release it.
  function releaseAllManaged() {
    for (const region of [...claimedByMe]) { releaseInert(region, OWNER); claimedByMe.delete(region); }
  }

  function relocate() {
    const host = openDialogHost();
    if (toggle.parentNode !== host) {
      releaseAllManaged();
      host.append(toggle, panel);
    }
  }

  // Relocating alone is not enough: the set of "other regions" to inert is
  // relative to whatever element toggle/panel currently live in, so a host
  // change must always be followed by recomputing inert state against the
  // NEW host — otherwise closing a dialog while Doc is still open at phone
  // width would move Doc back to document.body without ever making body's
  // own children inert, breaking the trap. Watching `inert` too (not just a
  // dialog's `open`) makes Doc reactive to a wholly separate modal — the
  // record panel on business.html — claiming or releasing the page on its
  // own schedule, which is what lets the toggle's own reachability track it.
  const dialogWatcher = typeof MutationObserver === 'function'
    ? new MutationObserver(() => {
        const priorHost = toggle.parentNode;
        relocate();
        applyModality();
        // A native dialog's own close() frequently resets focus to <body>
        // rather than restoring it. If that just happened while Doc is
        // still open (pinned), bring focus back inside Doc instead of
        // leaving it stranded on <body> — Tab from there would walk into
        // whatever the closing host left behind, not into Doc.
        if (state.open && toggle.parentNode !== priorHost) {
          const strandedOnBody = document.activeElement === document.body
            || !document.body.contains(document.activeElement);
          if (strandedOnBody) focusWithoutScrolling(dom.title);
        }
      })
    : null;
  dialogWatcher?.observe(document.documentElement, { attributes: true, attributeFilter: ['open', 'inert'], subtree: true });

  function applyModality() {
    const modal = isModal();
    panel.setAttribute('role', modal ? 'dialog' : 'complementary');
    if (modal) panel.setAttribute('aria-modal', 'true'); else panel.removeAttribute('aria-modal');
    const shouldCoverForDoc = modal && state.open;
    const regions = otherRegions();
    // Some OTHER, uncoordinated modal (the record panel) may already be
    // covering part of the page. The toggle sits outside that modal's own
    // reach (it is not `[data-panel-background]`), so without this check it
    // would stay reachable even while that modal is genuinely up — which is
    // exactly the gap the round-3 review flagged.
    const externalModalActive = regions.some((region) => region !== toggle && isClaimedByOther(region, OWNER));
    for (const region of regions) {
      const wantsInert = region === toggle
        ? (shouldCoverForDoc || (externalModalActive && !state.open))
        : shouldCoverForDoc;
      if (wantsInert) {
        claimInert(region, OWNER);
        claimedByMe.add(region);
      } else if (claimedByMe.has(region)) {
        releaseInert(region, OWNER);
        claimedByMe.delete(region);
      }
    }
  }

  function open() {
    relocate();
    state.open = true;
    panel.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    applyModality();
    focusWithoutScrolling(dom.title);
    renderContext();
  }

  function close({ returnFocus = true } = {}) {
    state.open = false;
    panel.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    applyModality();
    if (returnFocus) focusWithoutScrolling(toggle);
  }

  function togglePin() {
    state.pinned = !state.pinned;
    writePinned(state.pinned);
    dom.pin.setAttribute('aria-pressed', String(state.pinned));
    dom.pin.textContent = state.pinned ? 'Pinned' : 'Pin';
  }

  function renderContext() {
    const context = source.getContext() || {};
    dom.context.textContent = context.dealId
      ? `Working on: ${context.label || context.dealId}.`
      : 'Working on: nothing open yet. Commands that need a record will say so.';
  }
  // Exposed at module scope (see refreshDocContext below) so a host page can
  // re-render the label the moment its notion of "the open deal" changes —
  // not only on Doc's own open(), which is the only place that called this
  // before. Without it, a Doc opened before any deal, or pinned across a
  // deal-dialog close, kept showing a stale "Working on" line that no longer
  // matched the deal commands actually write to.
  currentRenderContext = renderContext;

  function appendLog(text, tone, { retry = null } = {}) {
    const line = document.createElement('p');
    line.className = `doc-log-line doc-log-${tone}`;
    line.textContent = text;
    if (retry) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'doc-log-retry';
      button.textContent = 'Retry';
      button.addEventListener('click', retry);
      line.append(' ', button);
    }
    dom.log.append(line);
    dom.log.scrollTop = dom.log.scrollHeight;
  }

  /**
   * Runs one command attempt against a context CAPTURED by the caller — never
   * re-read from source.getContext() in here. Each failed line's Retry button
   * closes over its own { command, text, context, idempotencyKey } object
   * (built below), not a shared mutable slot: clicking an older Retry after a
   * newer attempt has already succeeded or failed re-runs exactly what THAT
   * line originally attempted, against the deal that was open when it first
   * ran, however many other attempts have happened since. `idempotencyKey`,
   * when supplied, is a RETRY of the exact same attempt and reuses that key
   * so the server's own idempotency_key dedupe (mcp-server/src/tools.js
   * withEnvelope) applies — a retry can never turn into a second write, and
   * an unrelated later success can never make an older retry throw, because
   * nothing here reads state that a later call could have mutated out from
   * under it.
   */
  async function attempt({ command, text, context, idempotencyKey = null }) {
    const client = source.getClient();
    const receipt = await runCommand(client, command, text, context, idempotencyKey ? { idempotencyKey } : {});
    const { text: line } = formatReceiptLine(receipt);
    const drift = retryContextDriftNote(context, source.getContext());
    const fullLine = line + drift;
    if (receipt.status === 'refused' || receipt.status === 'unavailable') {
      const captured = { command, text, context, idempotencyKey: receipt.idempotencyKey };
      appendLog(fullLine, receipt.status, { retry: () => attempt(captured) });
    } else {
      appendLog(fullLine, receipt.status);
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    const raw = dom.input.value;
    if (!raw.trim()) return;
    const { command, text } = parseDocInput(raw);
    appendLog(`You: ${raw.trim()}`, 'you');
    dom.input.value = '';
    dom.input.disabled = true;
    try {
      const context = source.getContext() || {};
      await attempt({ command, text, context });
    } finally {
      dom.input.disabled = false;
      dom.input.focus();
    }
  }

  function containFocus(event) {
    if (event.key !== 'Tab' || !isModal()) return;
    const stops = [...panel.querySelectorAll(FOCUSABLE)];
    const active = document.activeElement;
    const target = panelTabTarget({
      inside: panel.contains(active),
      stopIndex: stops.indexOf(active),
      stopCount: stops.length,
      shiftKey: event.shiftKey,
    });
    if (!target) return;
    event.preventDefault();
    if (target === 'title') return focusWithoutScrolling(dom.title);
    focusWithoutScrolling(target === 'first' ? stops[0] : stops[stops.length - 1]);
  }

  toggle.addEventListener('click', () => (state.open ? close() : open()));
  dom.close.addEventListener('click', () => close());
  dom.pin.addEventListener('click', togglePin);
  dom.pin.setAttribute('aria-pressed', String(state.pinned));
  dom.pin.textContent = state.pinned ? 'Pinned' : 'Pin';
  dom.form.addEventListener('submit', handleSubmit);
  panel.addEventListener('keydown', containFocus);
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !state.open) return;
    if (!escapeShouldClose({ pinned: state.pinned })) return;
    close();
  });
  phoneQuery?.addEventListener?.('change', applyModality);

  relocate();
  applyModality();
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
}

export const __internal = { listCommands };
