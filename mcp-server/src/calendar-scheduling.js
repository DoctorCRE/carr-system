const NOTE = 'Candidate openings from stored meetings, not confirmed availability. May include overnight hours. Check Outlook before booking.';

export function calendarScheduling(snapshot, section, now) {
  const window = { start: now.getTime(), end: now.getTime() + 86400000, duration: 30 * 60000 };
  const context = { window: { start: now.toISOString(), end: new Date(window.end).toISOString() },
    minimum_duration_minutes: 30, note: NOTE };
  const unavailable = { ...context, state: 'unavailable', reason: 'source_unavailable', items: [] };
  if (!section?.sources) return unavailable;
  const warnings = snapshot.warnings === undefined ? [] : snapshot.warnings;
  const allowedWarnings = new Set(['responses_unverified', 'group_invites_not_shown', 'recurring_unverified', 'follow_up_trimmed']);
  const observed = Date.parse(section.observed_at);
  if (section.sources.outlook_calendar.state === 'stale' || observed > now.getTime() || section.malformed_items ||
      section.items.length >= 100 || section.items.some(item => item.time_changed || item.conflict || (item.all_day && item.end <= item.start)) ||
      !Array.isArray(warnings) || warnings.some(warning => !warning || !allowedWarnings.has(warning.code) ||
        (warning.count !== undefined && (!Number.isSafeInteger(warning.count) || warning.count < 1))))
    return { ...unavailable, reason: 'calendar_coverage_insufficient' };
  const items = [];
  const intervals = section.items.map(item => [Date.parse(item.start), Date.parse(item.end)])
    .filter(([start, end]) => end > start && start < window.end && end > window.start)
    .sort((a, b) => a[0] - b[0]);
  let cursor = window.start;
  const gap = (start, end) => {
    if (end - start >= window.duration) items.push({ start: new Date(start).toISOString(), end: new Date(end).toISOString() });
  };
  for (const [start, end] of intervals) {
    gap(cursor, Math.min(start, window.end));
    cursor = Math.max(cursor, Math.min(end, window.end));
  }
  gap(cursor, window.end);
  return { ...context, state: items.length ? section.state === 'partial' ? 'partial' : 'ready' : 'empty', items };
}
