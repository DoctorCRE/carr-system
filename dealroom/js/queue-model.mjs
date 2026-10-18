// Pure projection of the Hermes carr-build board. Browser DOM stays in queue.js
// so Node contract tests can import this without a document.
export const QUEUE_COLUMNS = Object.freeze([
  { id: "backlog", label: "Backlog", statuses: ["triage", "todo"] },
  { id: "ready", label: "Ready", statuses: ["ready", "scheduled"] },
  { id: "running", label: "Running", statuses: ["running"] },
  { id: "review", label: "Review", statuses: ["review"] },
  { id: "blocked", label: "Blocked", statuses: ["blocked"] },
  { id: "done", label: "Done", statuses: ["done"] },
]);
export const QUEUE_STALE_MS = 120_000;

export function queueColumnFor(status) {
  return QUEUE_COLUMNS.find((column) => column.statuses.includes(String(status)))?.id || null;
}
export function queueIsStale(projectedAt, now = Date.now()) {
  const stamp = Date.parse(projectedAt || "");
  return !Number.isFinite(stamp) || now - stamp > QUEUE_STALE_MS;
}
function validEvent(event) {
  const card = event?.card;
  return event && typeof event.task_id === "string" && /^t_[a-z0-9]+$/i.test(event.task_id) &&
    card && typeof card.title === "string" && typeof card.summary !== "string" &&
    typeof event.summary === "string" && queueColumnFor(card.status);
}
export function queueProjection(events) {
  const latest = new Map();
  for (const event of Array.isArray(events) ? events : []) {
    if (!validEvent(event)) continue;
    const existing = latest.get(event.task_id);
    if (!existing || Date.parse(event.card.updated_at) >= Date.parse(existing.card.updated_at)) latest.set(event.task_id, event);
  }
  const cards = [...latest.values()].map((event) => ({ ...event.card, task_id: event.task_id, summary: event.summary }))
    .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
  const byColumn = Object.fromEntries(QUEUE_COLUMNS.map((column) => [column.id, []]));
  for (const card of cards) byColumn[queueColumnFor(card.status)].push(card);
  return { cards, byColumn };
}
