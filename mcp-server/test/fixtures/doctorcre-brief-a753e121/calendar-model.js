// V5-UX-B04 — the critical-dates Calendar: every decision, no DOM.
//
// WHERE THE DATES COME FROM. A critical_date is a deal's own row (LOI expiry,
// lease expiration, option window, earnout). The record layer returns them per
// deal, from `get-deal-room`, at every horizon; `today-triage` only carries the
// next fourteen days, which is a to-do list and not a calendar. So the calendar
// reads the board once (`deal-room-board`) and then each deal's own record,
// with a small bound on how many are in flight. Both verbs were already pinned
// in contracts/carr-interface.v1.json; this surface adds no verb.
//
// HONESTY RULES, the reason most of this file exists:
//   - A deal whose record could not be read is NAMED and the page is partial.
//     Its dates are not guessed and it is never shown as "no dates".
//   - A row whose date cannot be read as a calendar day is kept as UNDATED, so
//     missing is never shown as empty.
//   - A 401/403 on any read ends the whole read with nothing shown.
//
// CALENDAR ARITHMETIC IS DONE ON YYYY-MM-DD STRINGS, through UTC, so a date can
// never slide across midnight on a viewer's clock. Only "today" is local: it is
// the viewer's own calendar day.

export const CALENDAR_VIEWS = Object.freeze(["month", "week"]);
export const READ_CONCURRENCY = 4;
const UPCOMING_LIMIT = 40;

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const SHORT_MONTHS = MONTHS.map((name) => name.slice(0, 3));
const DAY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;
// node-pg serialises a DATE column as midnight UTC when a view forgets the
// `to_jsonb(...)#>>'{}'` cast. Only that exact shape is accepted as a day; any
// other time of day would mean guessing which calendar day was meant.
const MIDNIGHT_PATTERN = /^(\d{4}-\d{2}-\d{2})T00:00:00(?:\.0+)?(?:Z|[+-]00:?00)$/;

const KIND_LABEL = {
  loi_expiry: "LOI expiry",
  lease_expiration: "Lease expiration",
  option_window: "Option window",
  earnout: "Earnout",
};

/* ---------------------------------------------------------------- day values */

function asUtc(day) {
  const match = DAY_PATTERN.exec(day);
  return new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
}

function fromUtc(date) {
  return date.toISOString().slice(0, 10);
}

/** A calendar day, or null when the value is not exactly one. */
export function toDay(value) {
  if (typeof value !== "string") return null;
  const day = DAY_PATTERN.test(value) ? value : MIDNIGHT_PATTERN.exec(value)?.[1];
  if (!day) return null;
  return fromUtc(asUtc(day)) === day ? day : null;
}

/** "Oct 8", with the year only when it is not this year's; non-dates pass through. */
export function shortDay(value, today) {
  const day = toDay(value);
  if (!day) return value;
  const [year, month, date] = day.split("-");
  const label = `${SHORT_MONTHS[Number(month) - 1]} ${Number(date)}`;
  return year === String(today).slice(0, 4) ? label : `${label}, ${year}`;
}

/** Strict date-only contracts share the calendar validation without midnight coercion. */
export function isCalendarDay(value) {
  return typeof value === "string" && DAY_PATTERN.test(value) && toDay(value) === value;
}

/** The viewer's own calendar day. */
export function localToday(now = new Date()) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export function addDays(day, count) {
  const date = asUtc(day);
  date.setUTCDate(date.getUTCDate() + count);
  return fromUtc(date);
}

/** A month step that keeps the day of the month, clamped to the month's end. */
export function addMonths(day, count) {
  const date = asUtc(day);
  const target = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + count, 1));
  const lastDay = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  target.setUTCDate(Math.min(date.getUTCDate(), lastDay));
  return fromUtc(target);
}

export function daysBetween(from, to) {
  return Math.round((asUtc(to) - asUtc(from)) / 86_400_000);
}

function startOfWeek(day) {
  return addDays(day, -asUtc(day).getUTCDay());
}

function shortDate(day) {
  const date = asUtc(day);
  return `${SHORT_MONTHS[date.getUTCMonth()]} ${date.getUTCDate()}`;
}

/** Six Sunday-first weeks around the anchor's month. */
export function monthGrid(anchor) {
  const date = asUtc(anchor);
  const first = fromUtc(new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1)));
  const start = startOfWeek(first);
  const month = date.getUTCMonth();
  const days = Array.from({ length: 42 }, (_, index) => {
    const day = addDays(start, index);
    return { day, inMonth: asUtc(day).getUTCMonth() === month };
  });
  return { title: `${MONTHS[month]} ${date.getUTCFullYear()}`, days };
}

/** The anchor's Sunday-first week. */
export function weekStrip(anchor) {
  const start = startOfWeek(anchor);
  const days = Array.from({ length: 7 }, (_, index) => ({ day: addDays(start, index), inMonth: true }));
  const end = days[6].day;
  const [startYear, endYear] = [start.slice(0, 4), end.slice(0, 4)];
  let title;
  if (startYear !== endYear) title = `${shortDate(start)}, ${startYear} – ${shortDate(end)}, ${endYear}`;
  else if (start.slice(5, 7) !== end.slice(5, 7)) title = `${shortDate(start)} – ${shortDate(end)}, ${endYear}`;
  else title = `${shortDate(start)} – ${asUtc(end).getUTCDate()}, ${endYear}`;
  return { title, days };
}

/* ---------------------------------------------------------------- URL memory */

/** The view the address names; anything unrecognised falls back, never errors. */
export function parseCalendarState(search, today) {
  const params = new URLSearchParams(search || "");
  const view = CALENDAR_VIEWS.includes(params.get("view")) ? params.get("view") : "month";
  const anchor = toDay(params.get("d")) || today;
  const day = toDay(params.get("day"));
  return { view, anchor, day };
}

export function calendarHref(state) {
  const params = new URLSearchParams({ view: state.view, d: state.anchor });
  if (state.day) params.set("day", state.day);
  return `/calendar?${params}`;
}

/** One month or one week forward (1) or back (-1). The selected day stays. */
export function stepAnchor(state, direction) {
  const anchor = state.view === "week" ? addDays(state.anchor, 7 * direction) : addMonths(state.anchor, direction);
  return { ...state, anchor };
}

/** Which way the grid should move when the view changes from `before` to `after`. */
export function motionDirection(before, after) {
  if (!before) return "enter";
  if (before.view !== after.view) return after.view === "week" ? "zoom-in" : "zoom-out";
  if (before.anchor === after.anchor) return "none";
  return after.anchor > before.anchor ? "forward" : "back";
}

/* ------------------------------------------------------------------- motion */

/**
 * How near a date is, and the marker that says so. The marker is a still
 * shape: a double ring for today and anything overdue (urgent), a ringed dot
 * for this week (attention), a dot for the rest of the month (calm), and a
 * hollow ring for anything further out or settled (still).
 */
export function approach(entry, today) {
  const days = daysBetween(today, entry.day);
  const label = days === 0 ? "today" : days === 1 ? "tomorrow" : days === -1 ? "yesterday"
    : days > 0 ? `in ${days} days` : `${-days} days ago`;
  if (entry.settled) return { band: "settled", days, pulse: "still", label };
  if (days < 0) return { band: "overdue", days, pulse: "urgent", label };
  if (days === 0) return { band: "today", days, pulse: "urgent", label };
  if (days <= 7) return { band: "soon", days, pulse: "attention", label };
  if (days <= 30) return { band: "near", days, pulse: "calm", label };
  return { band: "later", days, pulse: "still", label };
}

/* ---------------------------------------------------------------- entries */

function words(slug) {
  const text = String(slug).replace(/[_-]+/g, " ").trim();
  return text ? text[0].toUpperCase() + text.slice(1) : "";
}

function kindLabel(kind) {
  if (!kind) return null;
  return KIND_LABEL[kind] || words(kind);
}

/**
 * One deal's critical_date rows as calendar entries. The live adapter passes
 * the record layer's own row ({id, kind, due_on, note, source, status}) plus a
 * label/date pair; the fixture adapter passes only {label, date}. Both read the
 * same way here, and nothing absent is filled in: a missing status is unknown,
 * not open.
 */
export function criticalDateEntries(deal, detail) {
  const entries = [];
  const undated = [];
  (detail?.critical_dates || []).forEach((row, index) => {
    const kind = typeof row?.kind === "string" && row.kind ? row.kind : null;
    const rawLabel = row?.label || row?.note || kind;
    const label = rawLabel && rawLabel === kind ? kindLabel(kind) : (rawLabel || "Critical date");
    const status = typeof row?.status === "string" && row.status ? row.status : null;
    const entry = {
      key: row?.id ? String(row.id) : `${deal.id}:${index}`,
      deal_id: deal.id,
      deal_name: deal.name || "Unnamed deal",
      day: toDay(row?.due_on ?? row?.date ?? null),
      label,
      kind,
      kind_label: kindLabel(kind),
      source: typeof row?.source === "string" && row.source ? row.source : null,
      status,
      settled: status !== null && status !== "open",
    };
    (entry.day ? entries : undated).push(entry);
  });
  return { entries, undated };
}

/** Open before settled, then by label, within each day. */
export function entriesByDay(entries) {
  const map = new Map();
  for (const entry of entries) {
    if (!map.has(entry.day)) map.set(entry.day, []);
    map.get(entry.day).push(entry);
  }
  for (const rows of map.values()) {
    rows.sort((a, b) => Number(a.settled) - Number(b.settled) || a.label.localeCompare(b.label) || a.key.localeCompare(b.key));
  }
  return map;
}

/** Open dates from the oldest overdue forward, nearest first. */
export function upcomingEntries(entries, today, limit = UPCOMING_LIMIT) {
  return entries
    .filter((entry) => !entry.settled)
    .sort((a, b) => a.day.localeCompare(b.day) || a.label.localeCompare(b.label))
    .slice(0, limit);
}

/* ------------------------------------------------------------------- the read */

const endedSession = (error) => error?.status === 401 || error?.status === 403;

/**
 * Read the board, then every deal's own record, at most `concurrency` at a
 * time. Returns what was read and what was not; it never throws.
 */
export async function readCalendar(client, { concurrency = READ_CONCURRENCY, onProgress } = {}) {
  const empty = { entries: [], undated: [], failed: [], dealCount: 0, readCount: 0 };
  let board;
  try {
    board = await client.getBoard({ workspace: "all" });
  } catch (error) {
    return { ...empty, status: endedSession(error) ? "unauthorized" : "unavailable" };
  }
  if (!Array.isArray(board?.deals)) return { ...empty, status: "unavailable" };

  const deals = board.deals.filter((deal) => deal && typeof deal.id === "string");
  const entries = [];
  const undated = [];
  const failed = [];
  let readCount = 0;
  let signedOut = false;
  let next = 0;

  async function worker() {
    while (!signedOut && next < deals.length) {
      const deal = deals[next++];
      try {
        const detail = await client.getDeal(deal.id);
        if (!Array.isArray(detail?.critical_dates)) throw new Error("critical_dates unreadable");
        const read = criticalDateEntries(deal, detail);
        entries.push(...read.entries);
        undated.push(...read.undated);
        readCount += 1;
      } catch (error) {
        if (endedSession(error)) {
          signedOut = true;
          onProgress?.({ ...empty, status: "unauthorized" });
          return;
        }
        failed.push({ deal_id: deal.id, deal_name: deal.name || "Unnamed deal" });
      }
      if (!signedOut) onProgress?.({
        status: "reading", entries: [...entries], undated: [...undated], failed: [...failed],
        dealCount: deals.length, readCount, actor: board.actor || null,
      });
    }
  }

  await Promise.all(Array.from({ length: Math.max(1, Math.min(concurrency, deals.length)) }, worker));
  if (signedOut) return { ...empty, status: "unauthorized" };
  failed.sort((a, b) => a.deal_name.localeCompare(b.deal_name));
  return { status: "ready", entries, undated, failed, dealCount: deals.length, readCount, actor: board.actor || null };
}

/** The one state the page shows. */
export function calendarPhase(result) {
  if (!result || result.status === "loading") return "loading";
  if (result.status === "unauthorized") return "unauthorized";
  if (result.status === "reading") return "reading";
  if (result.status !== "ready") return "unavailable";
  if (result.failed.length > 0) return "partial";
  if (result.entries.length === 0 && result.undated.length === 0) return "empty";
  return "ready";
}
