// The one HTML escaper for every Deal Room surface. Safe for text content and
// for double- or single-quoted attribute values. It is NOT a URL sanitizer: an
// href built from data must also come from a validated route (see
// workspace-command-center-model.js safeDestination).
export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char]));
}
