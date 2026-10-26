// The morning brief's calendar section. The Outlook intake on the partner's own
// machine publishes the next fourteen days of meetings as one display snapshot
// (publish-board-snapshot, board "calendar-agenda"); this shapes the next seven
// days of it. The snapshot is display data from Outlook's local store: it never
// asserts attendance, and cancellations or moves are only as current as the
// store copy it was read from, which is why every section says so.

export const CALENDAR_AGENDA_BOARD = "calendar-agenda";
export const CALENDAR_AGENDA_SCHEMA = "carr-calendar-agenda.v1";
const HORIZON_MS = 7 * 86_400_000;
const STALE_MS = 18 * 3_600_000;
const LIMIT = 100;

const instant = value => typeof value === "string" && Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;
const text = value => typeof value === "string" && value.trim() ? value.trim().slice(0, 500) : null;
const person = value => value && typeof value === "object"
  ? { name: text(value.name), email: text(value.email)?.toLowerCase() ?? null } : { name: null, email: null };

/** A brief section from the stored snapshot, or null when none was ever published. */
export function calendarSection(snapshot, now = new Date()) {
  if (snapshot === undefined || snapshot === null) return null;
  const nowMs = now.getTime();
  const observed = instant(snapshot?.observed_at);
  if (snapshot?.schema !== CALENDAR_AGENDA_SCHEMA || observed === null || !Array.isArray(snapshot.items))
    return { state: "unavailable", reason: "source_unavailable", items: [] };
  let malformed = 0;
  const items = [];
  for (const row of snapshot.items) {
    const start = instant(row?.start), end = instant(row?.end);
    if (start === null || end === null || end < start || !text(row?.title)) { malformed++; continue; }
    if (end <= nowMs || start >= nowMs + HORIZON_MS) continue;
    const attendees = Array.isArray(row.attendees) ? row.attendees.map(person) : [];
    items.push({ start: new Date(start).toISOString(), end: new Date(end).toISOString(),
      title: text(row.title), location: text(row.location), organizer: person(row.organizer),
      attendees, attendee_count: Number.isSafeInteger(row.attendee_count) ? row.attendee_count : attendees.length,
      conflict: row.conflict === true });
  }
  items.sort((a, b) => a.start.localeCompare(b.start) || a.title.localeCompare(b.title));
  const stale = nowMs - observed > STALE_MS;
  return {
    state: stale || malformed || items.length > LIMIT ? "partial" : items.length ? "ready" : "empty",
    source: "outlook_local_store", lifecycle: "unverified",
    note: "Read from Outlook's local store on the partner's Mac. Attendance is not confirmed, and a cancellation or move shows once the next copy is read.",
    observed_at: new Date(observed).toISOString(),
    sources: { outlook_calendar: { state: stale ? "stale" : "ready" } },
    ...(malformed ? { malformed_items: malformed } : {}),
    items: items.slice(0, LIMIT),
  };
}
