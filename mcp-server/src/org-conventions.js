// Org conventions: the read-only `rule` store's one human write door
// (WR-000562 S4, docs/memory/team-memory-design.md D1 and D16).
//
// Agents propose a convention as a team-memory section under
// conventions/proposed/<slug>. governance-queue lists those sections, and a
// partner's in-person approval calls adopt-rule, which hands the section to
// ops.adopt_rule (migration 1027): one transaction makes it a proposed rule,
// activates it through the existing Joe-authority approval chain and retires
// the proposal. rule-cards reads the adopted cards back for
// ops/sync-core-rule-cards.py --from-store.
import { ToolError } from "./tool-error.ts";

const PLACEMENTS = new Set(["CORE", "PROMPT", "TOOL", "PLANNING"]);

// The same grammar ops.adopt_rule enforces: "Convention: <statement>" on the
// first line, an optional "Card: <json object>" line, then free notes.
function parseProposal(text) {
  const statement = /^Convention: ([^\n]+)/.exec(String(text ?? ""))?.[1]?.trim();
  if (!statement) return { adoptable: false, problem: "first line must be 'Convention: <statement>'" };
  const cardText = /\nCard: (\{[^\n]*\})[ \t]*(?:\n|$)/.exec(text)?.[1];
  const packsText = /\nPacks: ([^\n]*)/.exec(text)?.[1];
  if (cardText === undefined) {
    const packs = [...new Set((packsText ?? "").split(",").map((p) => p.trim()).filter(Boolean))].sort();
    if (!packs.length) return { adoptable: false, problem: "a convention without a card needs a 'Packs: <pack>, <pack>' line" };
    return { adoptable: true, statement, card: null, packs };
  }
  if (packsText !== undefined) return { adoptable: false, problem: "a card is delivered by the core block; drop its Packs line" };
  let card;
  try { card = JSON.parse(cardText); } catch { return { adoptable: false, problem: "Card line is not JSON" }; }
  if (!card || typeof card !== "object" || Array.isArray(card) || !card.id || "wording" in card
      || !PLACEMENTS.has(card.placement))
    return { adoptable: false, problem: "Card needs an id and a placement and must not carry wording" };
  return { adoptable: true, statement, card, packs: [] };
}

// governance-queue's pending_convention_proposals lane, read by
// ops.read_governance_queue() (migration 1027), with each proposal's
// adoptability and the adopt-rule call that approves it.
export function conventionProposals(rows = []) {
  return rows.map(({ body_text, ...row }) => {
    const parsed = parseProposal(body_text);
    return { ...row, base_version: Number(row.base_version), adoptable: parsed.adoptable,
      ...(parsed.adoptable
        ? { statement: parsed.statement, card_id: parsed.card?.id ?? null, packs: parsed.packs,
            approve_with: { verb: "adopt-rule", section_id: row.section_id, base_version: Number(row.base_version) } }
        : { problem: parsed.problem }) };
  });
}

const ADOPT_REFUSALS = new Map([
  ["convention_proposal_not_found", "convention_proposal_not_found"],
  ["convention_proposal_malformed", "convention_proposal_malformed"],
  ["convention_card_already_active", "convention_card_already_active"],
  ["convention_pack_unknown", "convention_pack_unknown"],
  ["version_conflict", "version_conflict"],
]);

// The server-written via of the app's Deal Room cookie session (dealroom-web.js).
const APP_APPROVAL_VIA = "dealroom-cookie";

export function orgConventionTools({ withEnvelope, writeEvent }) {
  const adopt = (c, actor, args) => withEnvelope(c, actor, "adopt-rule", args, async () => {
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
    await writeEvent(c, actor, "adopt-rule", "rule", adopted.rule_id,
      { new: { section_id: args.section_id, card_id: adopted.card_id, load_layer: adopted.load_layer, packs: adopted.packs },
        idempotency_key: args.idempotency_key });
    return adopted;
  });
  return {
    "adopt-rule": {
      write: true, humanOnly: true, humanCallerOnly: true, authorityOnly: true,
      description: "A partner adopts one team-memory convention proposal (conventions/proposed/<slug>, listed by governance-queue) as an active rule, in person: the rule's statement and card come from the proposal, a card-less convention is delivered by the existing packs its Packs line names, Joe's approval chain activates it, and the proposal retires in the same transaction. Only the app's signed-in approval surface can call it; machine identities and chat connector sessions are refused.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        section_id: { type: "string", description: "the proposal section from governance-queue pending_convention_proposals" },
        base_version: { type: "integer", description: "the proposal's current version" },
        reason: { type: "string", description: "why the partner adopts it" } },
        required: ["idempotency_key", "section_id", "base_version", "reason"] },
      handler: async (c, actor, args) => {
        // A partner's own claude.ai or Desktop connector session authenticates
        // as the verified partner, so humanCallerOnly alone would admit a chat
        // model acting in Joe's name (design D1). Adoption happens only in the
        // app: the Deal Room cookie gate is the one server-written via for it.
        if (actor.via !== APP_APPROVAL_VIA)
          throw new ToolError({ error: "rule_adoption_requires_app_approval" });
        return adopt(c, actor, args);
      },
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
