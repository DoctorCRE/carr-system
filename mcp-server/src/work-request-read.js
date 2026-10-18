// Canonical lifecycle crosswalk from work-request-projection.v1.json.
export const WORK_REQUEST_PROJECTION_STATE = Object.freeze({
  captured: "queued", triaged: "queued", ready: "queued", claimed: "claimed",
  in_progress: "in_progress", verification: "in_progress", awaiting_release: "in_progress",
  released: "in_progress", confirmed_closed: "completed", needs_joe: "needs_answer",
  blocked: "needs_answer", declined: "declined", superseded: "declined", failed: "failed",
});

export const WORK_REQUEST_READ_STATES = Object.freeze(Object.keys(WORK_REQUEST_PROJECTION_STATE));

// The alias is supplied by these queries, never by a caller. Private doctrine
// requests stay private in both the census and the card.
export function readableWorkRequestSql(alias) {
  return `${alias}.state in (${WORK_REQUEST_READ_STATES.map(state => `'${state}'`).join(",")})
    and (${alias}.doctrine_section_id is null or exists (
      select 1 from public.doctrine_section readable_section
      join public.doctrine_document readable_document on readable_document.id=readable_section.document_id
      where readable_section.id=${alias}.doctrine_section_id and readable_document.visibility='shared'))`;
}
