// rule-boot.js — the gated rule boot's text, rendered from the store.
//
// WHY THIS EXISTS. A session or subagent that has not read the rules cannot
// follow them, and until this module nothing put the rules in front of a
// context before it acted: standing-context returned one-line gists when a
// session remembered to call it. Joe asked for 100% recall on relevant rules.
// The design (Jev, p=1.00): every context fetches, through standing-context
// `detail: "boot"`, (1) a one-line index of EVERY active rule and (2) the FULL
// TEXT of the always-on set, and hooks/rule-boot-gate.py denies every other
// tool call in that context until all pages of the current digest are fetched.
//
// DATABASE AND CODE ONLY. There is no rule file anywhere: statements come from
// v_compiled_rules at request time; the class, summary and when-line per rule
// come from ops/config/rule-classes.v1.json through the generated module
// rule-boot-classes.js. Nothing here is a cache of the store.
//
// PAGED because a tool result is capped (MCP output ~25k tokens; a Bash result
// ~30k characters, and `./run.sh call` prints JSON, whose escaping inflates the
// text). RULE_BOOT_PAGE_CHARS keeps a page comfortably inside both.
//
// DETERMINISTIC: rules sorted by short id, no clock in the text, so the digest
// is a pure function of (statements, classes) and a page is stable across calls.
import {
  RULE_BOOT_BUDGET_TOKENS,
  RULE_BOOT_CHARS_PER_TOKEN,
  RULE_BOOT_CLASSES,
  RULE_BOOT_CLASSES_DIGEST,
} from "./rule-boot-classes.js";

export const RULE_BOOT_SCHEMA = "carr-rule-boot/v1";
export const RULE_BOOT_PAGE_CHARS = 20000;

const UNCLASSIFIED_SUMMARY =
  "(unclassified since the last classification; its full text is in the always-on section)";
const UNCLASSIFIED_WHEN = "until classified: treat it as always relevant";

// One view of the corpus for ONE sponsor. The SQL that feeds this already
// scopes personal rules to the authenticated sponsor; the two checks below are
// the belt to that brace, so a mis-scoped row can never render.
function scopedRules(rows, sponsor, classes) {
  const byId = new Map();
  for (const r of rows || []) {
    const id = String(r.id || "").slice(0, 8).toLowerCase();
    if (!/^[0-9a-f]{8}$/.test(id)) continue;
    const owner = r.personal_to || null;
    if (owner && owner !== sponsor) continue;
    const cls = classes[id] || null;
    if (cls && cls.personal_to && cls.personal_to !== sponsor) continue;
    byId.set(id, { id, statement: String(r.statement || "").trim(), personal: Boolean(owner), cls });
  }
  return [...byId.values()].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

// The whole boot text for one sponsor. Returns the text and the counts; the
// digest is taken by the caller (async Web Crypto).
export function renderRuleBoot(rows, sponsor, classes = RULE_BOOT_CLASSES) {
  const rules = scopedRules(rows, sponsor, classes);
  const alwaysOn = rules.filter(r => !r.cls || r.cls.on);
  const unclassified = rules.filter(r => !r.cls).map(r => r.id);
  const out = [];
  out.push("# CARR RULE BOOT — read every page before acting");
  out.push("");
  out.push(`Served live from the CARR doctrine store (the only source of truth) for ${sponsor ? `sponsor ${sponsor}` : "an unsponsored runtime (shared rules only)"}.`);
  out.push("Part 1 is the FULL TEXT of retained rules. Each binds at its stated moment, not on every turn.");
  out.push("Part 2 is a one-line INDEX of every active rule: `id | class | summary | when it applies`.");
  out.push("Classes: A always-on (full text in Part 1); B binds at an action point; C binds when a topic is present;");
  out.push("D already enforced by a gate; E stale or duplicate; U unclassified (full text in Part 1).");
  out.push("If a rule's full text is missing from Part 1, fetch its binding text:");
  out.push("standing-context with rule_ids:[\"<id>\"]. Never quote an index summary as the rule itself.");
  out.push(`Classification: ${RULE_BOOT_CLASSES_DIGEST}.`);
  out.push("");
  out.push(`## PART 1 — ALWAYS-ON RULES, FULL TEXT (${alwaysOn.length})`);
  out.push("");
  for (const r of alwaysOn) {
    out.push(`### ${r.id}${r.personal ? " (personal)" : ""}`);
    out.push(r.statement);
    out.push("");
  }
  out.push(`## PART 2 — INDEX OF EVERY ACTIVE RULE (${rules.length})`);
  out.push("");
  for (const r of rules) {
    const cls = r.cls ? r.cls.cls.toUpperCase() : "U";
    const summary = r.cls ? r.cls.summary : UNCLASSIFIED_SUMMARY;
    const when = r.cls ? r.cls.when : UNCLASSIFIED_WHEN;
    out.push(`${r.id} | ${cls} | ${summary} | ${when}`);
  }
  out.push("");
  out.push("## END OF RULE BOOT");
  const text = out.join("\n") + "\n";
  return {
    text,
    counts: { rules: rules.length, always_on: alwaysOn.length, index: rules.length,
              unclassified: unclassified.length },
    unclassified,
    index_ids: rules.map(r => r.id),
    always_on_ids: alwaysOn.map(r => r.id),
  };
}

// Split on line boundaries so no rule id or index line is cut across pages.
// A single line longer than a page is split hard, which only a pathological
// statement could trigger; the concatenation of all pages is always the text.
export function paginate(text, limit = RULE_BOOT_PAGE_CHARS) {
  const pages = [];
  let current = "";
  for (const line of text.split(/(?<=\n)/)) {
    if (current.length + line.length > limit && current) {
      pages.push(current);
      current = "";
    }
    if (line.length > limit) {
      for (let i = 0; i < line.length; i += limit) pages.push(line.slice(i, i + limit));
      continue;
    }
    current += line;
  }
  if (current) pages.push(current);
  return pages.length ? pages : [""];
}

async function sha256Hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, "0")).join("");
}

export function ruleBootFetchCall(page) {
  return `standing-context {"detail":"boot","page":${page}}`;
}

// One page of the boot for one sponsor. `page` is 1-based; out of range is a
// typed error the caller turns into a ToolError.
export async function ruleBootPage(rows, sponsor, page = 1, classes = RULE_BOOT_CLASSES) {
  const rendered = renderRuleBoot(rows, sponsor, classes);
  const digest = `sha256:${await sha256Hex(rendered.text)}`;
  const pages = paginate(rendered.text);
  const n = Number.isInteger(page) ? page : Number.parseInt(String(page ?? 1), 10);
  if (!Number.isInteger(n) || n < 1 || n > pages.length) {
    return { error: "page_out_of_range", page, pages_total: pages.length, digest };
  }
  const approxTokens = Math.round(rendered.text.length / RULE_BOOT_CHARS_PER_TOKEN);
  return {
    schema: RULE_BOOT_SCHEMA,
    digest,
    page: n,
    pages_total: pages.length,
    total_chars: rendered.text.length,
    approx_tokens: approxTokens,
    budget_tokens: RULE_BOOT_BUDGET_TOKENS,
    over_budget: approxTokens > RULE_BOOT_BUDGET_TOKENS,
    counts: rendered.counts,
    ...(rendered.unclassified.length ? { unclassified: rendered.unclassified } : {}),
    next: n < pages.length ? ruleBootFetchCall(n + 1)
      : "complete: every page of this digest has been read",
    text: pages[n - 1],
  };
}
