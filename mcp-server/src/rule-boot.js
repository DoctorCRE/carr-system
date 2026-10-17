import { canonicalSha256 } from "./jev-call-receipt.js";

export const RULE_BOOT_SCHEMA = "carr-rule-boot/v1";

// Hash the complete sponsor-visible corpus, without delivering its text at boot.
// Binding text is delivered by the scoped lookup before the relevant action.
export async function ruleBootPage(rows, sponsor, page = 1) {
  const rules = (rows || [])
    .filter(r => !r.personal_to || r.personal_to === sponsor)
    .map(r => ({ id: r.id, statement: r.statement, scope: r.scope || null, personal_to: r.personal_to || null }))
    .sort((a, b) => a.id.localeCompare(b.id));
  const digest = `sha256:${await canonicalSha256({ sponsor: sponsor || null, rules })}`;
  if (page !== 1) return { error: "page_out_of_range", page, pages_total: 1, digest };
  const personal = rules.filter(r => r.personal_to).length;
  const text = "CARR rule corpus digest. Before acting, use the standing-context scoped lookup for applicable binding text. "
    + "Fetch explicit rule_ids through standing-context when needed. This boot receipt delivers no rule text.\n";
  return {
    schema: RULE_BOOT_SCHEMA, digest, page: 1, pages_total: 1,
    total_chars: text.length, counts: { rules: rules.length, shared: rules.length - personal, personal },
    next: "complete: every page of this digest has been read", text,
  };
}
