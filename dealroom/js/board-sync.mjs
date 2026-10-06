/**
 * Board sync: the Deal Room's authoritative-read coordinator.
 *
 * Pure coordinator. No DOM, no network, no storage, no timers, and no clock of
 * its own beyond an injected `now`. Everything that touches the page or the
 * wire is injected by app.js, so the async sequences this module exists to get
 * right — an overlapping refresh, a slow or failed read, a snapshot that lands
 * after the partner already changed something — are EXECUTED in tests rather
 * than described by them.
 *
 * WHY IT EXISTS
 *
 * The board used to take its displayed values from two places at once:
 * `deal-room-board` (a current snapshot) and the `/pipeline/changes` feed (an
 * ascending cursor over the WHOLE deal event log). The feed's early pages are
 * history, so a page of old events replayed old field values over the current
 * snapshot, and two overlapping reads could land in either order. This module
 * makes the split explicit and one-directional:
 *
 *   - VALUES come only from a board snapshot. One board read at a time; a
 *     snapshot is applied only if it is still the current one when it arrives.
 *   - The FEED keeps everything it always carried — receipts, presence, capture
 *     sessions, the base-event map, the changed markers — and ASKS for a fresh
 *     snapshot when it carries news. It never writes a value.
 *
 * WHAT IT DOES NOT PROMISE
 *
 *   - No latency claim. Displayed values are as fresh as the LAST SUCCESSFUL
 *     board read and no fresher. `status().last_read_at` is that moment, and
 *     the surface states it rather than implying a live stream.
 *   - No completeness claim about the feed: it is a cursor, and a session still
 *     draining history is behind by construction (see change-receipts.mjs).
 *   - No write-conflict protection. `patch-deal-field`'s base_event_id gate is
 *     unchanged, and it still cannot see a change made to the same deal through
 *     a different verb. That limitation is the server's and is untouched here.
 *   - No lag compensation. A local hold is released once a board read that
 *     STARTED AFTER the write was confirmed has been applied — which is exactly
 *     the read-after-write behaviour the board already relies on. Nothing here
 *     can detect, or make up for, a backend that answered from behind.
 */

/**
 * The states the badge can truthfully be in. This is DERIVED from the health of
 * the two read paths, never set directly — which is the point: a working change
 * feed cannot clear a failed board read, and a fresh board cannot hide a feed
 * that stopped answering.
 */
export const SYNC_STATES = Object.freeze({
  /** Nothing has been read yet this session. */
  STARTING: 'starting',
  /** A read a person is waiting on is open; the last values still stand. */
  SYNCING: 'syncing',
  /** The board has been read and neither path is failing. */
  READY: 'ready',
  /** A read failed. Values on screen are the last successful read's. */
  RECONNECTING: 'reconnecting',
  /** A snapshot arrived and could not be shown. Not a connection problem. */
  ERROR: 'error',
  /** The browser says there is no network. */
  OFFLINE: 'offline',
});

/**
 * What is known about ONE read path. Board and feed carry their own, because
 * they answer different questions: the board says what the values ARE, the feed
 * says what has happened. Either can be broken while the other works.
 */
export const HEALTH = Object.freeze({
  /** Never read in this session. */
  UNREAD: 'unread',
  /** Its last read succeeded. */
  OK: 'ok',
  /** Its last read did not answer, or the connection went away under it. */
  FAILED: 'failed',
  /** Board only: the read answered and the page could not render it. */
  RENDER_FAILED: 'render_failed',
});

/**
 * Does this feed page carry anything the board's own values could depend on?
 *
 * Deliberately broad: any event about a deal counts, including one about a deal
 * this session has never seen, because that is exactly what a newly created
 * record looks like to a board that was empty. The answer only ever costs one
 * coalesced board read, so a false yes is cheap and a false no is a stale row.
 */
export function batchTouchesBoard(events) {
  for (const event of Array.isArray(events) ? events : []) {
    if (!event || typeof event !== 'object') continue;
    if (event.subject_type && event.subject_type !== 'deal') continue;
    if (!event.subject_id) continue;
    return true;
  }
  return false;
}

/**
 * The values a confirmed local action put on screen, held until a board read
 * that could actually contain them has been applied.
 *
 * `at` is a position in the page's own monotonic ordering, not a timestamp: the
 * only question ever asked of it is "was this write confirmed before or after
 * that read was issued", and two clocks cannot disagree about that.
 */
export function createHolds() {
  const byDeal = new Map();
  return {
    /** @param {string} dealId @param {Object} patch deal record fields @param {number} at */
    note(dealId, patch, at) {
      if (!dealId || !patch || typeof patch !== 'object') return;
      const entry = byDeal.get(dealId) || new Map();
      for (const [field, value] of Object.entries(patch)) entry.set(field, { value, at });
      byDeal.set(dealId, entry);
    },
    /** Drop everything a read issued at `throughAt` would already contain. */
    release(throughAt) {
      for (const [dealId, entry] of byDeal) {
        for (const [field, hold] of entry) if (hold.at <= throughAt) entry.delete(field);
        if (!entry.size) byDeal.delete(dealId);
      }
    },
    get(dealId) { return byDeal.get(dealId) || null; },
    get size() {
      let total = 0;
      for (const entry of byDeal.values()) total += entry.size;
      return total;
    },
    list() {
      const out = [];
      for (const [dealId, entry] of byDeal) {
        for (const [field, hold] of entry) out.push({ deal_id: dealId, field, value: hold.value, at: hold.at });
      }
      return out;
    },
  };
}

/**
 * Fold held local values into a board snapshot.
 *
 * A snapshot is authoritative for every value EXCEPT one this page confirmed
 * after the read was issued: that read cannot have contained it, so applying it
 * would put an older value back under the partner's cursor. Held values are
 * copied onto the snapshot's own rows; a hold for a deal the board no longer
 * returns is not resurrected, because existence is the board's to state.
 *
 * @returns a new board object; the input is never mutated.
 */
export function mergeBoardSnapshot(board, holds, startedAt) {
  const source = Array.isArray(board?.deals) ? board.deals : [];
  const heldFields = [];
  const deals = source.map((deal) => {
    const copy = { ...deal };
    const entry = deal && holds && typeof holds.get === 'function' ? holds.get(deal.id) : null;
    if (!entry) return copy;
    for (const [field, hold] of entry) {
      if (hold.at > startedAt) {
        copy[field] = hold.value;
        heldFields.push(`${deal.id}|${field}`);
      }
    }
    return copy;
  });
  return { ...board, deals, held_fields: heldFields };
}

/**
 * Re-resolve a row captured before a snapshot against the board as it stands.
 *
 * A snapshot REPLACES every row object — that is what stops a stale read from
 * bleeding into a newer one — so anything that captured a row earlier (an open
 * review agenda, a form, a queued list) is holding a detached copy that will
 * never change again. IDENTITY IS THE id, NEVER THE OBJECT: hold ids, and
 * resolve them at the moment of use.
 *
 * The captured copy is returned unchanged when the board no longer holds that
 * id, so a record that has left the board is shown as it was last seen rather
 * than vanishing mid-sequence. Nothing is invented for it either way.
 *
 * @param {Object|null} captured a row read from an earlier snapshot
 * @param {Map|Array} rows the current rows, by id or as a list
 */
export function resolveCurrentRow(captured, rows) {
  if (!captured || !captured.id || !rows) return captured || null;
  const current = typeof rows.get === 'function'
    ? rows.get(captured.id)
    : (Array.isArray(rows) ? rows.find((row) => row && row.id === captured.id) : null);
  return current || captured;
}

function describe(error) {
  const text = error?.message || String(error ?? 'unknown error');
  return text.length > 200 ? `${text.slice(0, 199)}…` : text;
}

/**
 * @param {Object} deps
 * @param {() => Promise<any>} deps.readBoard authoritative snapshot (deal-room-board)
 * @param {(cursor:string|null) => Promise<any>} deps.readChanges the existing changes cursor
 * @param {(board:any, meta:Object) => void} deps.applyBoard called ONLY with a current snapshot
 * @param {(status:Object) => void} [deps.onStatus]
 * @param {() => number} [deps.now]
 */
export function createBoardSync({
  readBoard, readChanges, applyBoard, onStatus = null, now = () => Date.now(),
} = {}) {
  if (typeof readBoard !== 'function') throw new TypeError('createBoardSync needs readBoard');
  if (typeof readChanges !== 'function') throw new TypeError('createBoardSync needs readChanges');
  if (typeof applyBoard !== 'function') throw new TypeError('createBoardSync needs applyBoard');

  const holds = createHolds();
  const counters = {
    board_reads: 0, board_applied: 0, board_superseded: 0, board_failed: 0,
    board_coalesced: 0, board_abandoned: 0, board_rehomed: 0,
    change_reads: 0, change_applied: 0, change_superseded: 0, change_failed: 0,
    change_skipped: 0, change_abandoned: 0,
    refresh_requests: 0,
  };

  // One monotonic counter orders every local event that matters: a read being
  // issued and a write being confirmed. Nothing compares wall-clock times.
  let clock = 0;
  // Bumped when everything in flight stops being trusted (going offline, or a
  // deliberate invalidation).
  let generation = 1;
  // Identity of the newest read STARTED of each kind. An answer from an older
  // one is discarded rather than applied late. Abandoning a read bumps these
  // too, so a request that may never answer cannot block or overtake the next.
  let boardSeq = 0;
  let pollSeq = 0;
  let snapshots = 0;
  let cursor = null;
  let boardRun = null;
  // At most one queued follow-up read, held as a deferred so it can be answered
  // either by starting when its turn comes or by being re-homed onto a read
  // that superseded it: {reason, quiet, cancelled, settled, resolve, promise}.
  let queued = null;
  let pollRun = null;
  // The ticket id of the read a person is waiting on, or null. Held by id
  // rather than counted, so a read that never answers cannot leave the badge
  // stuck saying "syncing".
  let foregroundTicket = null;
  let online = true;
  let boardHealth = HEALTH.UNREAD;
  let feedHealth = HEALTH.UNREAD;
  let boardError = null;
  let feedError = null;
  let lastBoardReadAt = null;
  let lastChangeReadAt = null;

  const OFFLINE_REASON = 'The browser reported no network connection.';

  /**
   * The badge state, derived from both paths every time it is asked for.
   *
   * The order is the whole correction: a failure on EITHER path outranks a
   * success on the other, so a poll that answers cannot repaint over a board
   * read that did not, and a fresh snapshot cannot hide a feed that stopped.
   */
  function derivedState() {
    if (!online) return SYNC_STATES.OFFLINE;
    if (boardHealth === HEALTH.RENDER_FAILED) return SYNC_STATES.ERROR;
    if (boardHealth === HEALTH.FAILED || feedHealth === HEALTH.FAILED) return SYNC_STATES.RECONNECTING;
    if (foregroundTicket !== null) return SYNC_STATES.SYNCING;
    if (boardHealth === HEALTH.OK) return SYNC_STATES.READY;
    return SYNC_STATES.STARTING;
  }

  function snapshotStatus() {
    return Object.freeze({
      state: derivedState(),
      online,
      generation,
      /** How many authoritative snapshots have actually been applied. */
      snapshots,
      cursor,
      board_health: boardHealth,
      feed_health: feedHealth,
      board_error: boardError,
      feed_error: feedError,
      /** The board's trouble first: it is the one that decides the values. */
      last_error: boardError || feedError,
      /** When the last board read SUCCEEDED. Displayed values are that old. */
      last_read_at: lastBoardReadAt,
      last_change_read_at: lastChangeReadAt,
      board_read_in_flight: Boolean(boardRun),
      refresh_pending: Boolean(queued),
      held_fields: holds.size,
    });
  }

  function publish() {
    const status = snapshotStatus();
    if (onStatus) onStatus(status);
    return status;
  }

  function releaseForeground(ticketId) {
    if (foregroundTicket === ticketId) foregroundTicket = null;
  }

  /**
   * Stop waiting on the open board read. Its answer can no longer apply (the
   * sequence has moved past it) and it no longer blocks the next one — the
   * escape hatch for a request that may never come back.
   */
  function abandonBoardRead() {
    if (!boardRun) return false;
    boardSeq += 1;
    boardRun = null;
    foregroundTicket = null;
    counters.board_abandoned += 1;
    return true;
  }

  /** The same for the change feed, and for the same reason. */
  function abandonPoll() {
    if (!pollRun) return false;
    pollSeq += 1;
    pollRun = null;
    counters.change_abandoned += 1;
    return true;
  }

  /**
   * Take the queued follow-up out of the queue and answer its callers.
   *
   * Without this, a queued request chained onto an abandoned read would fire
   * when that read finally settled, start a THIRD read behind the current one,
   * and supersede the answer the page was actually waiting for.
   *
   * Its callers are never left hanging on the read it was chained to: the
   * returned function RE-HOMES them onto the read that replaced it, or answers
   * them directly when there is no replacement.
   *
   * @returns {((replacement: Promise|null) => void)|null}
   */
  function detachQueued(why) {
    if (!queued) return null;
    const entry = queued;
    queued = null;
    entry.cancelled = why || 'superseded';
    if (entry.settled) return null;
    entry.settled = true;
    counters.board_rehomed += 1;
    return (replacement) => entry.resolve(replacement || Promise.resolve({
      ok: boardHealth === HEALTH.OK, applied: false, reason: 'rehomed',
      detail: entry.cancelled, ticket: null,
    }));
  }

  /** Drop the queued follow-up with no read to hand its callers on to. */
  function cancelQueued(why) {
    const rehome = detachQueued(why);
    if (rehome) rehome(null);
    return Boolean(rehome);
  }

  function startBoardRead({ reason, quiet }) {
    boardSeq += 1;
    clock += 1;
    const ticket = {
      id: boardSeq, generation, started_at: clock, reason: reason || 'refresh', quiet: Boolean(quiet),
    };
    counters.board_reads += 1;
    // A background read says nothing while it is open: the values on screen are
    // still the last successful read's, which is what the badge already states.
    // A read after a failure keeps saying "reconnecting" until one succeeds,
    // because derivedState puts the failure first.
    if (!ticket.quiet) foregroundTicket = ticket.id;
    publish();
    const run = (async () => {
      let board = null;
      let error = null;
      try {
        board = await readBoard();
      } catch (caught) {
        error = caught;
      }
      releaseForeground(ticket.id);
      // The generation guard: this answer is only allowed to touch the page if
      // it is still the newest read of its kind AND nothing has invalidated the
      // view since it was issued. A superseded answer changes no health, no
      // clock and no value — it is not evidence about now.
      if (ticket.generation !== generation || ticket.id !== boardSeq) {
        counters.board_superseded += 1;
        publish();
        return { ok: !error, applied: false, reason: 'superseded', error, ticket };
      }
      if (error) {
        counters.board_failed += 1;
        boardHealth = HEALTH.FAILED;
        boardError = describe(error);
        publish();
        return { ok: false, applied: false, reason: 'read_failed', error, ticket };
      }
      const merged = mergeBoardSnapshot(board, holds, ticket.started_at);
      try {
        applyBoard(merged, {
          generation, ticket, snapshot: snapshots + 1, held_fields: merged.held_fields,
        });
      } catch (caught) {
        // A render fault is not a read fault: nothing is retried and no hold is
        // released, because nothing reached the page. It is also NOT ready —
        // the screen is older than the answer we just threw away.
        counters.board_failed += 1;
        boardHealth = HEALTH.RENDER_FAILED;
        boardError = describe(caught);
        publish();
        return { ok: false, applied: false, reason: 'apply_failed', error: caught, ticket };
      }
      snapshots += 1;
      holds.release(ticket.started_at);
      lastBoardReadAt = now();
      counters.board_applied += 1;
      boardHealth = HEALTH.OK;
      boardError = null;
      publish();
      return { ok: true, applied: true, reason: 'applied', board: merged, ticket };
    })();
    const tracked = run.then((outcome) => {
      if (boardRun === tracked) boardRun = null;
      return outcome;
    });
    boardRun = tracked;
    return tracked;
  }

  /**
   * Read the board, serialized.
   *
   * While a read is open, further requests do NOT start a second one and do NOT
   * simply join the open one — an open read may have been issued before the
   * caller's own write was confirmed, so joining it would answer with data that
   * cannot contain it. They collapse into exactly ONE follow-up read that starts
   * when the current one settles, and every caller waiting gets that one.
   *
   * `force` abandons the open read instead of queueing behind it: the escape
   * hatch for a read that may never answer (a reconnect after the network went
   * away). It also CANCELS any queued follow-up — otherwise that follow-up
   * would fire when the abandoned read finally settled and overtake the forced
   * read that replaced it.
   */
  function refreshBoard({ reason = 'refresh', force = false, quiet = false } = {}) {
    counters.refresh_requests += 1;
    if (force) {
      // Order matters: take the queued work out FIRST so it cannot fire later,
      // abandon the read it was chained to, start the replacement, and only
      // then hand the waiting callers onto that replacement.
      const rehome = detachQueued(`superseded by a forced ${reason} read`);
      abandonBoardRead();
      const run = startBoardRead({ reason, quiet });
      if (rehome) rehome(run);
      return run;
    }
    // The queue is checked BEFORE the open read, not after. There is a window
    // of one microtask between a read being cleared and its follow-up starting,
    // and a request landing in it would see no open read, start its own, and
    // then be superseded by the follow-up firing behind it — two concurrent
    // reads from a coordinator whose whole job is that there is one.
    if (queued) {
      counters.board_coalesced += 1;
      return queued.promise;
    }
    if (!boardRun) return startBoardRead({ reason, quiet });
    const entry = { reason, quiet, cancelled: null, settled: false, resolve: null, promise: null };
    entry.promise = new Promise((resolve) => { entry.resolve = resolve; });
    const fire = () => {
      if (queued === entry) queued = null;
      // Already answered by detachQueued: this entry was superseded while the
      // read it was waiting on was still open.
      if (entry.settled) return;
      entry.settled = true;
      entry.resolve(startBoardRead({ reason: entry.reason, quiet: entry.quiet }));
    };
    boardRun.then(fire, fire);
    queued = entry;
    publish();
    return entry.promise;
  }

  /** A background refresh: no badge change while it is open, one read at most. */
  function requestRefresh(reason = 'change-feed') {
    return refreshBoard({ reason, quiet: true });
  }

  /**
   * Poll the changes cursor, serialized.
   *
   * A poll while one is already open is SKIPPED rather than queued: the caller
   * is an interval, and the next tick will ask again. That is what stops a slow
   * page and a fast one from landing out of order — and the cursor is advanced
   * only by an answer that is still current, so a discarded page is simply
   * delivered again rather than moving the cursor backwards.
   *
   * `force` is the bounded escape from a feed read that never answers: skipping
   * while one is open is right for an interval and wrong forever, so a reconnect
   * abandons the open read and starts a fresh one. The abandoned answer, if it
   * ever lands, moves nothing — including the cursor.
   */
  function pollChanges({ force = false } = {}) {
    if (pollRun && !force) {
      counters.change_skipped += 1;
      return Promise.resolve({ ok: true, applied: false, reason: 'busy', changes: null });
    }
    if (pollRun) abandonPoll();
    pollSeq += 1;
    clock += 1;
    const ticket = { id: pollSeq, generation, started_at: clock, cursor };
    counters.change_reads += 1;
    const run = (async () => {
      let payload = null;
      let error = null;
      try {
        payload = await readChanges(ticket.cursor);
      } catch (caught) {
        error = caught;
      }
      if (ticket.generation !== generation || ticket.id !== pollSeq) {
        counters.change_superseded += 1;
        return { ok: !error, applied: false, reason: 'superseded', changes: null, error, ticket };
      }
      if (error) {
        counters.change_failed += 1;
        feedHealth = HEALTH.FAILED;
        feedError = describe(error);
        publish();
        return { ok: false, applied: false, reason: 'read_failed', changes: null, error, ticket };
      }
      cursor = payload && payload.cursor !== undefined && payload.cursor !== null
        ? payload.cursor : cursor;
      lastChangeReadAt = now();
      counters.change_applied += 1;
      // A working feed says the FEED works, and nothing else. Whether the values
      // are current is the board's health to report, so this only ever clears
      // the feed's own trouble.
      feedHealth = HEALTH.OK;
      feedError = null;
      publish();
      return { ok: true, applied: true, reason: 'applied', changes: payload, ticket };
    })();
    const tracked = run.then((outcome) => {
      if (pollRun === tracked) pollRun = null;
      return outcome;
    });
    pollRun = tracked;
    return tracked;
  }

  return {
    refreshBoard,
    requestRefresh,
    pollChanges,

    /**
     * Record a value this page put on screen because the server confirmed the
     * write. It survives any snapshot from a read issued before this moment.
     * @param {string} dealId
     * @param {Object} patch deal-record fields, exactly as the board holds them
     */
    noteLocalWrite(dealId, patch) {
      clock += 1;
      holds.note(dealId, patch, clock);
      publish();
      return holds.size;
    },

    /**
     * Stop trusting anything currently in flight: answers already sent are
     * dropped, queued work is cancelled, and BOTH open reads are abandoned so
     * neither can block nor overtake what comes next.
     */
    invalidate() {
      generation += 1;
      cancelQueued('invalidated');
      abandonBoardRead();
      abandonPoll();
      publish();
      return generation;
    },

    /**
     * The browser's own network signal.
     *
     * Going offline drops everything in flight — an answer that arrives after
     * the connection went away is not evidence about now — and marks both paths
     * failed, because what is on screen is no longer known to be current.
     * Coming back says only that we are trying again: the health stays failed
     * until a read actually succeeds.
     */
    setOnline(next) {
      const value = Boolean(next);
      if (value === online) return snapshotStatus();
      online = value;
      generation += 1;
      cancelQueued(value ? 'reconnected' : 'went offline');
      abandonBoardRead();
      abandonPoll();
      if (!value) {
        boardHealth = HEALTH.FAILED;
        feedHealth = HEALTH.FAILED;
        boardError = OFFLINE_REASON;
        feedError = OFFLINE_REASON;
      }
      return publish();
    },

    status: snapshotStatus,
    holds: () => holds.list(),
    stats: () => ({ ...counters }),
  };
}
