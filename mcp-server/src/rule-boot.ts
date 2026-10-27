type RuleRow = { id: string; statement: string; scope?: unknown; personal_to?: string | null };

// Page and hash the sponsor-visible rule corpus.
import { digest } from "./artifact-trust.js";

export const RULE_BOOT_SCHEMA = "carr-rule-boot/v1";

// Hash the complete sponsor-visible corpus, without delivering its text at boot.
// Binding text is delivered by the scoped lookup before the relevant action.
export async function ruleBootPage(rows: RuleRow[] | null | undefined, sponsor: string | null | undefined, page: number | string = 1) {
  const rules = (rows || [])
    .filter(r => !r.personal_to || r.personal_to === sponsor)
    .map(r => ({ id: r.id, statement: r.statement, scope: r.scope || null, personal_to: r.personal_to || null }))
    .sort((a, b) => a.id.localeCompare(b.id));
  const corpusDigest = digest({ sponsor: sponsor || null, rules });
  if (Number(page) !== 1) return { error: "page_out_of_range", page, pages_total: 1, digest: corpusDigest };
  const personal = rules.filter(r => r.personal_to).length;
  const text = "CARR rule corpus digest. Before acting, use the standing-context scoped lookup for applicable binding text. "
    + "Fetch explicit rule_ids through standing-context when needed. This boot receipt delivers no rule text.\n";
  return {
    schema: RULE_BOOT_SCHEMA, digest: corpusDigest, page: 1, pages_total: 1,
    total_chars: text.length, counts: { rules: rules.length, shared: rules.length - personal, personal },
    next: "complete: every page of this digest has been read", text,
  };
}
