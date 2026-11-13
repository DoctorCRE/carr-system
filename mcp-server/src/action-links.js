// Resolve credential-free draft addresses without granting write authority.
import { DRAFT_ELIGIBILITY_SQL } from "./lead-draft-eligibility.ts";
import { authorizationClassForActor } from "./identity.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const REF = /^al1_([A-Za-z0-9_-]{1,120})$/;
const ORIGIN = "https://app.doctorcre.com";

export const ACTION_LINK_SCHEMA_VERSION = "action-link.v1";
export const ACTION_LINK_RESOLVE_SCHEMA_VERSION = "action-link-resolve.v1";

const KIND_CODES = Object.freeze({ lead_draft: "ld", reply_draft: "rd" });
const CODE_KINDS = Object.freeze(Object.fromEntries(Object.entries(KIND_CODES).map(([kind, code]) => [code, kind])));

export function buildActionLink(card) {
  const keys = card && typeof card === "object" ? Object.keys(card) : [];
  const reply = card?.kind === "reply_draft";
  const expected = reply ? ["kind", "id", "owner", "version"] : ["kind", "id"];
  if (keys.length !== expected.length || !expected.every(key => keys.includes(key))
    || (reply && (!["joe", "dell"].includes(card.owner) || !Number.isSafeInteger(card.version) || card.version < 1 || card.version > 2_147_483_647))
    || !Object.hasOwn(KIND_CODES, card.kind) || !UUID.test(String(card.id)))
    throw new Error("action_link_input_invalid");
  const identity = `${KIND_CODES[card.kind]}:${card.id.toLowerCase()}${reply ? `:${card.owner}:${card.version}` : ""}`;
  const ref = `al1_${Buffer.from(identity).toString("base64url")}`;
  const path = `/a/${ref}`;
  return { schema_version: ACTION_LINK_SCHEMA_VERSION, ref, path, url: `${ORIGIN}${path}` };
}

/** The card a reference names, or null for anything that is not a well-formed reference. */
export function parseActionLinkRef(ref) {
  const match = typeof ref === "string" ? REF.exec(ref) : null;
  if (!match) return null;
  const [code, id, ...rest] = Buffer.from(match[1], "base64url").toString("utf8").split(":");
  if (!Object.hasOwn(CODE_KINDS, code) || !UUID.test(id ?? "")) return null;
  if (code === "rd") {
    const [owner, version] = rest;
    if (rest.length !== 2 || !["joe", "dell"].includes(owner) || !/^[1-9][0-9]{0,9}$/.test(version ?? "") || Number(version) > 2_147_483_647) return null;
    const card = { kind: "reply_draft", id, owner, version: Number(version) };
    return buildActionLink(card).ref === ref ? card : null;
  }
  if (rest.length) return null;
  return { kind: CODE_KINDS[code], id };
}

export function actionLinkTools({ ToolError, readTool }) {
  const refuse = error => { throw new ToolError({ error }); };
  return {
    "resolve-action-link": {
      description: "Resolve one credential-free lead or reply draft reference for its signed-in owner. Lead drafts retain action-link-resolve.v1. Version-pinned reply drafts return action-link-resolve.v2 with exact submit-email-draft arguments for explicit human approval through the existing domain command. Resolution never approves, queues or sends. Unknown and foreign cards refuse action_link_not_found; retired or consumed cards refuse action_link_no_longer_actionable; edited replies refuse action_link_stale. Addresses expire by lifecycle, without a clock TTL.",
      inputSchema: { type: "object", additionalProperties: false,
        properties: { ref: { type: "string", maxLength: 124 } }, required: ["ref"] },
      handler: async (c, actor, args) => {
        const card = parseActionLinkRef(args.ref);
        if (!card) refuse("action_link_not_found");
        if (!actor?.id) refuse("action_link_not_found");
        if (card.kind === "reply_draft") {
          if (authorizationClassForActor(actor) !== "verified_partner") refuse("human_only_verb_requires_verified_partner");
          if (card.owner !== actor.slug) refuse("action_link_not_found");
          const current = async () => {
            const row = (await c.query(`select p.id,p.kind,p.owner,p.version,p.status,
                coalesce(a.email_context#>>'{lifecycle,state}','live') as source_state
              from email_proposal p join activity a on a.id=p.activity_id where p.id=$1::uuid`, [card.id])).rows[0];
            if (!row || row.kind !== "reply_draft" || row.owner !== card.owner) refuse("action_link_not_found");
            if (row.status !== "pending" || row.source_state !== "live") refuse("action_link_no_longer_actionable");
            if (row.version !== card.version) refuse("action_link_stale");
          };
          await current();
          const result = await readTool(c, actor, "list-email-proposals", { proposal_id: card.id });
          const d = result.proposals[0];
          if (!d || d.id !== card.id || d.kind !== card.kind || d.reply_draft?.owner !== card.owner) refuse("action_link_not_found");
          if (d.version !== card.version) refuse("action_link_stale");
          if (d.status !== "pending" || d.confirm_verb !== "submit-email-draft") refuse("action_link_no_longer_actionable");
          await current();
          return {
            schema_version: "action-link-resolve.v2", ref: args.ref,
            card: { kind: card.kind, id: d.id, state: "pending_approval", source_version: d.version,
              subject: d.reply_draft.subject, body: d.reply_draft.body, to: d.reply_draft.to, cc: d.reply_draft.cc,
              actions: [{ action: "approve", verb: "submit-email-draft", args: {
                proposal_id: d.id, base_version: d.version, subject: d.reply_draft.subject,
                body: d.reply_draft.body, to: d.reply_draft.to, cc: d.reply_draft.cc,
              } }],
            },
          };
        }
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
