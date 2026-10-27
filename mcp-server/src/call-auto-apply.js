// Server-side bar a pipeline-call change must clear to auto-apply instead of staying a card.
const words = text => String(text ?? "").trim().split(/\s+/).filter(Boolean).length;

export function automaticApplyHold(candidate) {
  const { kind, payload = {}, evidence_quote: quote } = candidate;
  if (!["next_step", "phase_move"].includes(kind)) return `A ${kind} change is never applied automatically.`;
  if (payload.needs_review) return String(payload.needs_review);
  if (words(quote) < 4) return "The quoted evidence is missing or too short to trust.";
  if (kind === "phase_move" && !String(payload.value ?? "").trim()) return "The call did not state a phase.";
  if (kind === "next_step") {
    if (!payload.next_date) return "The call did not state a date for the next step.";
    if (words(payload.text) < 2) return "The next step is too short to apply without a person reading it.";
  }
  return null;
}
