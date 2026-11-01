// Org conventions: the read-only `rule` store's one write door
// (WR-000562 S4, docs/memory/team-memory-design.md D1 and D16).
//
// Agents write a convention as a team-memory section under
// conventions/proposed/<slug>. governance-queue lists those sections, and a
// partner or their sponsored agent calls adopt-rule, which hands the section to
// ops.adopt_rule: one transaction activates any replacement through the
// existing Joe-authority approval chain, retires the named rules and
// consumes the proposal. rule-cards reads the adopted cards back for
// ops/sync-core-rule-cards.py --from-store.
import { ToolError } from "./tool-error.ts";

const PLACEMENTS = new Set(["CORE", "PROMPT", "TOOL", "PLANNING"]);

// Mirrors ops.adopt_rule: Convention with Card or Packs, optionally Retires;
// a retirement-only proposal starts with Retires and has no delivery metadata.
function parseProposal(text) {
  text = String(text ?? "");
  const retireLines = text.match(/^Retires:.*$/gm) ?? [];
  let retires = [];
  if (retireLines.length) {
    try {
      const ids = JSON.parse(retireLines[0].slice("Retires: ".length));
      if (retireLines.length !== 1 || !Array.isArray(ids) || !ids.length
          || ids.some(id => typeof id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)))
        throw new Error("invalid retirement targets");
      retires = [...new Set(ids.map(id => id.toLowerCase()))].sort();
    } catch { return { adoptable: false, problem: "Retires needs a nonempty JSON array of full rule UUIDs" }; }
  }
  const statement = /^Convention: ([^\n]+)/.exec(text)?.[1]?.trim();
  if (!statement) {
    if (retires.length && text.startsWith("Retires: ") && !/^(Card|Packs|Convention):/m.test(text))
      return { adoptable: true, statement: null, card: null, packs: [], retires };
    return { adoptable: false, problem: "first line must be 'Convention: <statement>' or 'Retires: [<rule UUID>, ...]'" };
  }
  const cardText = /\nCard: (\{[^\n]*\})[ \t]*(?:\n|$)/.exec(text)?.[1];
  const packsText = /\nPacks: ([^\n]*)/.exec(text)?.[1];
  if (cardText === undefined) {
    const packs = [...new Set((packsText ?? "").split(",").map((p) => p.trim()).filter(Boolean))].sort();
    if (!packs.length) return { adoptable: false, problem: "a convention without a card needs a 'Packs: <pack>, <pack>' line" };
    return { adoptable: true, statement, card: null, packs, retires };
  }
  if (packsText !== undefined) return { adoptable: false, problem: "a card is delivered by the core block; drop its Packs line" };
  let card;
  try { card = JSON.parse(cardText); } catch { return { adoptable: false, problem: "Card line is not JSON" }; }
  if (!card || typeof card !== "object" || Array.isArray(card) || !card.id || "wording" in card
      || !PLACEMENTS.has(card.placement))
    return { adoptable: false, problem: "Card needs an id and a placement and must not carry wording" };
  return { adoptable: true, statement, card, packs: [], retires };
}

// governance-queue's pending_convention_proposals lane, read by
// ops.read_governance_queue() (migration 1027), with each proposal's
// adoptability and the adopt-rule call that approves it.
export async function conventionProposals(rows = [], c) {
  const proposals = rows.map(({ body_text, ...row }) => ({ row, parsed: parseProposal(body_text) }));
  const ids = [...new Set(proposals.flatMap(({ parsed }) => parsed.retires ?? []))];
  const rules = ids.length ? (await c.query(
    "select id, statement, status from v_rule_lookup where id=any($1::uuid[])", [ids])).rows : [];
  return proposals.map(({ row, parsed }) => {
    const retirements = (parsed.retires ?? []).map(id => {
      const rule = rules.find(r => r.id === id);
      return { rule_id: id, statement: rule?.statement ?? null, status: rule?.status ?? null };
    });
    const problem = retirements.some(r => !["proposed", "active"].includes(r.status)) ? "every retirement target must be a proposed or active rule" : parsed.problem;
    const adoptable = parsed.adoptable && !problem;
    const statement = [...retirements.map(r => `retires rule ${r.rule_id}: ${r.statement}`),
      ...(parsed.statement ? [parsed.statement] : [])].join("\n");
    return { ...row, base_version: Number(row.base_version), adoptable,
      ...(adoptable
        ? { statement, replacement_statement: parsed.statement, retires: parsed.retires, retirements,
            card_id: parsed.card?.id ?? null, packs: parsed.packs,
            approve_with: { verb: "adopt-rule", section_id: row.section_id, base_version: Number(row.base_version) } }
        : { problem }) };
  });
}

const ADOPT_REFUSALS = new Map([
  ["convention_proposal_not_found", "convention_proposal_not_found"],
  ["convention_proposal_malformed", "convention_proposal_malformed"],
  ["convention_card_already_active", "convention_card_already_active"],
  ["convention_pack_unknown", "convention_pack_unknown"],
  ["version_conflict", "version_conflict"],
  ["convention_retirement_rule_not_found", "convention_retirement_rule_not_found"],
  ["convention_retirement_rule_not_active", "convention_retirement_rule_not_active"],
]);

export function orgConventionTools({ withEnvelope, writeEvent }) {
  const adopt = (c, actor, args) => withEnvelope(c, actor, "adopt-rule", args, async () => {
    const queue = (await c.query('select ops.read_governance_queue() as queue')).rows[0].queue;
    const section = queue.pending_convention_proposals.find(row => row.section_id === args.section_id);
    if (section) {
      const parsed = parseProposal(section.body_text);
      if (!parsed.adoptable) throw new ToolError({ error: 'convention_proposal_malformed', section_id: args.section_id,
        field_errors: [{ field: 'proposal', value: section.body_text, code: 'convention_proposal_malformed', pattern: parsed.problem }] });
      if (parsed.packs.length) {
        const allowed = (await c.query('select pack from ops.rule_pack_index() order by pack')).rows.map(row => row.pack);
        if (parsed.packs.some(pack => !allowed.includes(pack)))
          throw new ToolError({ error: 'convention_pack_unknown', section_id: args.section_id,
            field_errors: [{ field: 'Packs', value: parsed.packs, code: 'convention_pack_unknown', allowed_values: allowed }] });
      }
    }
    let adopted;
    try {
      adopted = (await c.query("select ops.adopt_rule($1::uuid,$2,$3,$4) as adopted",
        [args.section_id, args.base_version, args.idempotency_key, args.reason])).rows[0].adopted;
    } catch (error) {
      const code = ADOPT_REFUSALS.get(error.message);
      if (code) throw new ToolError({ error: code, section_id: args.section_id });
      // Backstop for two proposals with one card id: the document lock in
      // ops.adopt_rule serializes them, and the partial unique index refuses
      // whatever still races past it.
      if (error.code === "23505" && error.constraint === "rule_active_card_id")
        throw new ToolError({ error: "convention_card_already_active", section_id: args.section_id });
      if (/requires Joe authority|human authority/.test(error.message))
        throw new ToolError({ error: "rule_adoption_requires_joe" });
      throw error;
    }
    await writeEvent(c, actor, "adopt-rule", adopted.rule_id ? "rule" : "doctrine_section", adopted.rule_id ?? args.section_id,
      { new: { section_id: args.section_id, retired_rule_ids: adopted.retired_rule_ids, card_id: adopted.card_id, load_layer: adopted.load_layer, packs: adopted.packs },
        idempotency_key: args.idempotency_key });
    return adopted;
  });
  return {
    "adopt-rule": {
      // Joe, 2026-10-31: "If you think a rule is needed you just make it a rule
      // and tell me you did it." humanOnly admits a partner or the agent they
      // sponsor; the event row records which actor adopted.
      write: true, humanOnly: true, authorityOnly: true,
      description: `Adopt a team-memory convention proposal (conventions/proposed/<slug>, listed by governance-queue) through Joe's existing authority chain. A partner or the agent they sponsor adopts directly and tells Joe what it adopted. A Retires JSON-array line retires named proposed or active rules with the reason and proposal reference. Convention with JSON Card or Packs creates an optional replacement, scoped by its own statement as the moment; retiring a pending proposed rule into a replacement is how an agent activates its current wording. Card: WR698.S1.J1 is PR attribution and is ignored; only Card: {"id":"K01","placement":"CORE"} is rule-card metadata. To amend an adopted rule, create a new proposal with Retires: ["<full rule UUID>"]. Retirement-only adoption creates no rule. All effects and proposal consumption are atomic.`,
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        section_id: { type: "string", description: "the proposal section from governance-queue pending_convention_proposals" },
        base_version: { type: "integer", description: "the proposal's current version" },
        reason: { type: "string", description: "why the rule is adopted or retired" } },
        required: ["idempotency_key", "section_id", "base_version", "reason"] },
      handler: adopt,
    },

    "rule-cards": {
      write: false,
      description: "Read every active rule that carries a rule card, in the ops/config/rule-cards.v1.json card shape (wording = the rule's statement). ops/sync-core-rule-cards.py --from-store renders the cards file and the CLAUDE.md/AGENTS.md block from this.",
      inputSchema: { type: "object", additionalProperties: false, properties: {} },
      handler: async c => {
        const rows = (await c.query(
          `select id, statement, card from v_rule_lookup
            where status = 'active' and card is not null order by card->>'id'`)).rows;
        return { ok: true, count: rows.length, cards: rows.map(r => ({ ...r.card, wording: r.statement,
          word_count: r.statement.split(/\s+/).filter(Boolean).length, rule_id: r.id })) };
      },
    },
  };
}
