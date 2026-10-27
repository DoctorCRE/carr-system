// The morning brief's calendar section. The Outlook intake on the partner's own
// machine publishes the next fourteen days of their meetings as one display
// snapshot (publish-board-snapshot, board "calendar-agenda"); this shapes the
// next seven days of it. The snapshot is display data from Outlook's local
// store. It carries the gaps the reader knows about as warning codes, each one
// becomes a plain sentence here, and a section with any of them is partial,
// never ready.

export const CALENDAR_AGENDA_BOARD = "calendar-agenda";
const CALENDAR_AGENDA_SCHEMA = "carr-calendar-agenda.v1";
const HORIZON_MS = 7 * 86_400_000;
const STALE_MS = 18 * 3_600_000;
const LIMIT = 100;
const plural = (n, one, many) => (n === 1 ? one : many);
const WARNINGS = {
  responses_unverified: () => "May include meetings you declined or that were cancelled or deleted. Check Outlook.",
  group_invites_not_shown: () => "Invites sent only to a group you belong to are not shown.",
  undecoded: n => `${n} calendar ${plural(n, "item", "items")} in Outlook couldn't be read, so meetings may be missing.`,
  time_changed: n => `${n} ${plural(n, "meeting has", "meetings have")} more than one stored time. Check Outlook for the current time.`,
  recurring_unverified: () => "Recurring meetings show only the occurrences Outlook stored; some may be missing.",
  follow_up_trimmed: n => `${n} meeting outcome ${plural(n, "review was", "reviews were")} left out to keep the brief small.`,
  trimmed: n => `${n} later ${plural(n, "meeting was", "meetings were")} left out to keep the brief small.`,
};

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
      all_day: row.all_day === true, recurring: row.recurring === true, time_changed: row.time_changed === true,
      times: Array.isArray(row.times)
        ? row.times.map(instant).filter(t => t !== null).map(t => new Date(t).toISOString()) : [],
      conflict: row.conflict === true });
  }
  items.sort((a, b) => a.start.localeCompare(b.start) || a.title.localeCompare(b.title));
  for (const item of items) item.schedule_conflict = false;
  for (let i = 0; i < items.length; i++) {
    const a = items[i];
    if (a.all_day || a.end <= a.start) continue;
    for (let j = i + 1; j < items.length && items[j].start < a.end; j++) {
      const b = items[j];
      if (!b.all_day && b.end > b.start) a.schedule_conflict = b.schedule_conflict = true;
    }
  }
  const notes = [];
  for (const warning of Array.isArray(snapshot.warnings) ? snapshot.warnings : []) {
    const count = Number.isSafeInteger(warning?.count) && warning.count > 0 ? warning.count : 1;
    const say = Object.prototype.hasOwnProperty.call(WARNINGS, warning?.code) ? WARNINGS[warning.code] : null;
    notes.push(say ? say(count) : "This calendar has a gap the brief cannot describe. Check Outlook.");
  }
  if (malformed) notes.push(`${malformed} stored ${plural(malformed, "meeting", "meetings")} could not be shown.`);
  if (items.length > LIMIT) notes.push(`Only the first ${LIMIT} meetings are shown.`);
  const stale = nowMs - observed > STALE_MS;
  const future = observed > nowMs;
  const reviews = [];
  let malformedReviews = 0;
  if (snapshot.follow_up_reviews !== undefined && !Array.isArray(snapshot.follow_up_reviews)) malformedReviews++;
  for (const row of Array.isArray(snapshot.follow_up_reviews) ? snapshot.follow_up_reviews : []) {
    const start = instant(row?.start), end = instant(row?.end);
    if (row?.state !== "needs_review" || typeof row?.key !== "string" || !/^[a-f0-9]{16}$/.test(row.key)
        || !text(row?.title) || !text(row?.prompt) || start === null || end === null || end <= start) {
      malformedReviews++; continue;
    }
    if (stale || future || end > observed || end > nowMs || end <= nowMs - 86_400_000) continue;
    reviews.push({ key: row.key, title: text(row.title), start: new Date(start).toISOString(),
      end: new Date(end).toISOString(), state: "needs_review", prompt: text(row.prompt) });
  }
  reviews.sort((a, b) => b.end.localeCompare(a.end) || a.key.localeCompare(b.key));
  if (reviews.length) notes.push("These are scheduled meetings, not confirmed attendance. Confirm each happened before recording an outcome or next step.");
  if (malformedReviews) notes.push(`${malformedReviews} stored meeting outcome ${plural(malformedReviews, "review", "reviews")} could not be shown.`);
  if (reviews.length > LIMIT) notes.push(`Only the first ${LIMIT} meeting outcome reviews are shown.`);
  if (future) notes.push("This calendar's observation time is in the future. Refresh Outlook before reviewing meetings.");
  if (stale) notes.push("This calendar was last read from Outlook more than 18 hours ago.");
  return {
    state: notes.length ? "partial" : items.length ? "ready" : "empty",
    source: "outlook_local_store", notes,
    observed_at: new Date(observed).toISOString(),
    sources: { outlook_calendar: { state: stale ? "stale" : notes.length ? "partial" : "ready" } },
    ...(malformed ? { malformed_items: malformed } : {}),
    items: items.slice(0, LIMIT),
    ...(snapshot.follow_up_reviews !== undefined ? { follow_up_reviews: reviews.slice(0, LIMIT) } : {}),
  };
}
