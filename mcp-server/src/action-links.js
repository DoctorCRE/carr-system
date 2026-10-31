// One-tap action links (WR-000617 slice 5, server half).
//
// A link points at ONE card by its stable record id and carries nothing else:
// no token, no signed session, no auto-sign-in. Opening it still needs the
// partner's normal sign-in; resolve-action-link then answers for the signed-in
// actor only. The path shape also satisfies the notification deep_link rule
// (migration 0521: relative path, no scheme, host or query), so the morning
// brief, the outbox and the Dot can all carry it.

import { DRAFT_ELIGIBILITY_SQL } from "./lead-draft-eligibility.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const REF = /^al1_([A-Za-z0-9_-]{1,120})$/;
const ORIGIN = "https://app.doctorcre.com";

export const ACTION_LINK_SCHEMA_VERSION = "action-link.v1";
export const ACTION_LINK_RESOLVE_SCHEMA_VERSION = "action-link-resolve.v1";

// kind -> the short code inside the ref. Add a kind here when a card type joins.
const KIND_CODES = Object.freeze({ lead_draft: "ld" });
const CODE_KINDS = Object.freeze(Object.fromEntries(Object.entries(KIND_CODES).map(([kind, code]) => [code, kind])));

/** Build the link for one card. Accepts exactly {kind, id}; anything else is refused. */
export function buildActionLink(card) {
  const keys = card && typeof card === "object" ? Object.keys(card) : [];
  if (keys.length !== 2 || !keys.includes("kind") || !keys.includes("id")
    || !Object.hasOwn(KIND_CODES, card.kind) || !UUID.test(String(card.id)))
    throw new Error("action_link_input_invalid");
  const ref = `al1_${Buffer.from(`${KIND_CODES[card.kind]}:${card.id.toLowerCase()}`).toString("base64url")}`;
  const path = `/a/${ref}`;
  return { schema_version: ACTION_LINK_SCHEMA_VERSION, ref, path, url: `${ORIGIN}${path}` };
}

/** The card a reference names, or null for anything that is not a well-formed reference. */
export function parseActionLinkRef(ref) {
  const match = typeof ref === "string" ? REF.exec(ref) : null;
  if (!match) return null;
  const [code, id, ...rest] = Buffer.from(match[1], "base64url").toString("utf8").split(":");
  if (rest.length || !Object.hasOwn(CODE_KINDS, code) || !UUID.test(id ?? "")) return null;
  return { kind: CODE_KINDS[code], id };
}

export function actionLinkTools({ ToolError }) {
  const refuse = error => { throw new ToolError({ error }); };
  return {
    "resolve-action-link": {
      description: "Resolve one action-link reference (from buildActionLink / a morning-brief, email or Dot link) to the card's current state for the signed-in actor only. The link carries no credential and grants no access. Another partner's card and an unknown reference both refuse action_link_not_found; a card already approved or whose lead has moved refuses action_link_no_longer_actionable.",
      inputSchema: { type: "object", additionalProperties: false,
        properties: { ref: { type: "string", maxLength: 124 } }, required: ["ref"] },
      handler: async (c, actor, args) => {
        const card = parseActionLinkRef(args.ref);
        if (!card) refuse("action_link_not_found");
        // Only lead_draft exists today; a new kind adds its own branch here.
        const { rows: [d] } = await c.query(
          `select d.id, d.lead_id, d.owner_id, d.revision, d.approved_at, d.subject, d.body,
                  to_jsonb(d.scheduled_for)#>>'{}' as scheduled_for, d.time_zone,
                  (${DRAFT_ELIGIBILITY_SQL}) as eligible
             from lead_contact_draft d join lead l on l.id = d.lead_id join party p on p.id = l.party_id
            where d.id = $1::uuid`, [card.id]);
        // Ownership first, so another partner's card is indistinguishable from an unknown one.
        if (!d || !d.owner_id || d.owner_id !== actor.id) refuse("action_link_not_found");
        if (d.approved_at || !d.eligible)
          refuse("action_link_no_longer_actionable");
        return {
          schema_version: ACTION_LINK_RESOLVE_SCHEMA_VERSION, ref: args.ref,
          card: {
            kind: card.kind, id: d.id, lead_id: d.lead_id, state: "pending_approval", revision: d.revision,
            subject: d.subject, body: d.body, scheduled_for: d.scheduled_for, time_zone: d.time_zone,
            actions: [{ action: "approve", verb: "approve-lead-draft", args: { draft_id: d.id, revision: d.revision } }],
          },
        };
      },
    },
  };
}
