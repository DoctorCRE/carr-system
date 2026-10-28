// Server-side bar a pipeline-call change must clear to auto-apply instead of staying a card.
const words = text => String(text ?? "").trim().split(/\s+/).filter(word => /[a-z0-9]/i.test(word)).length;

export async function automaticApplyHold(candidate) {
  const { kind, payload = {}, evidence_quote: quote } = candidate;
  if (!["next_step", "phase_move"].includes(kind)) return `A ${kind} change is never applied automatically.`;
  if (payload.needs_review) return String(payload.needs_review);
  if (words(quote) < 4) return "The quoted evidence is missing or too short to trust.";
  if (kind === "phase_move" && !String(payload.value ?? "").trim()) return "The call did not state a phase.";
  if (kind === "next_step") {
    if (!payload.next_date) return "The call did not state a date for the next step.";
    if (words(payload.text) < 2) return "The next step is too short to apply without a person reading it.";
  }
  const source = payload.evidence_source;
  if (!["joe", "dell"].includes(source?.speaker))
    return "The quoted evidence does not resolve to a partner in the stored call.";
  if (!/^[a-f0-9]{64}$/.test(source.source_sha256 ?? ""))
    return "The stored call evidence receipt is missing.";
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(quote));
  const quoteHash = [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
  if (source.quote_sha256 !== quoteHash)
    return "The quoted evidence does not match the stored call evidence receipt.";
  return null;
}
