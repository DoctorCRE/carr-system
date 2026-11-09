// Derive candidate scheduling gaps from stored calendar coverage.
type CalendarItem = { start: string; end: string; time_changed?: unknown; conflict?: unknown };
type CalendarSection = {
  sources?: { outlook_calendar: { state: string } };
  observed_at: string;
  malformed_items?: unknown;
  items: CalendarItem[];
  state: string;
};

const NOTE =
  "Candidate openings from stored meetings, not confirmed availability. May include overnight hours. Check Outlook before booking.";

export function calendarScheduling(
  snapshot: { warnings?: unknown },
  section: CalendarSection | null | undefined,
  now: Date,
) {
  const window = { start: now.getTime(), end: now.getTime() + 86400000, duration: 30 * 60000 };
  const context = {
    window: { start: now.toISOString(), end: new Date(window.end).toISOString() },
    minimum_duration_minutes: 30,
    note: NOTE,
  };
  const unavailable = { ...context, state: "unavailable", reason: "source_unavailable", items: [] };
  if (!section?.sources) return unavailable;
  const warnings = snapshot.warnings === undefined ? [] : snapshot.warnings;
  const allowedWarnings = new Set([
    "responses_unverified",
    "group_invites_not_shown",
    "recurring_unverified",
    "follow_up_trimmed",
    "canceled_hidden",
  ]);
  const observed = Date.parse(section.observed_at);
  if (
    section.sources.outlook_calendar.state === "stale" ||
    observed > now.getTime() ||
    section.malformed_items ||
    section.items.length >= 100 ||
    section.items.some((item) => item.time_changed || item.conflict || item.end <= item.start) ||
    !Array.isArray(warnings) ||
    warnings.some(
      (warning) =>
        !warning ||
        typeof warning !== "object" ||
        !("code" in warning) ||
        typeof warning.code !== "string" ||
        !allowedWarnings.has(warning.code) ||
        ("count" in warning &&
          warning.count !== undefined &&
          (typeof warning.count !== "number" || !Number.isSafeInteger(warning.count) || warning.count < 1)),
    )
  )
    return { ...unavailable, reason: "calendar_coverage_insufficient" };
  const items: { start: string; end: string }[] = [];
  const intervals = section.items
    .map((item): [number, number] => [Date.parse(item.start), Date.parse(item.end)])
    .filter(([start, end]) => end > start && start < window.end && end > window.start)
    .sort((a, b) => a[0] - b[0]);
  let cursor = window.start;
  const gap = (start: number, end: number) => {
    if (end - start >= window.duration)
      items.push({ start: new Date(start).toISOString(), end: new Date(end).toISOString() });
  };
  for (const [start, end] of intervals) {
    gap(cursor, Math.min(start, window.end));
    cursor = Math.max(cursor, Math.min(end, window.end));
  }
  gap(cursor, window.end);
  return { ...context, state: items.length ? (section.state === "partial" ? "partial" : "ready") : "empty", items };
}
