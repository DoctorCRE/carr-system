// Server-derived partner authority for interactive partners and the two native
// implementation agents they sponsor. Caller input never reaches this module:
// identity.js derives the actor and sponsor from verified session state.

import { authorizationClassForActor } from "./identity.js";

// 'joe-local' and 'dell-local' ADDED 2026-08-26 (Joe's ruling, decision
// dc57f62d). These are the machine credentials `./run.sh call <verb>` presents
// through local-verb.mjs, and leaving them out was the SECOND half of the
// roadblock — the half that removing humanOnly does not touch.
//
// THE SYMPTOM, in Joe's words: "I'm literally telling you to do things and I'm
// getting blocked bc it's human only. It doesn't make any sense." What made it
// senseless is that the SAME verb answered differently depending on the door.
// review-and-triage through the connector succeeded and the ledger recorded
// actor_slug 'joe'; the identical verb through `./run.sh call` came back
// authority_connection_unavailable. Not humanOnly — that is a separate gate.
// This one: authorityOnly verbs need authorityDsnForActor, which asks
// partnerAuthoritySlugForActor, which returned null purely because the slug was
// missing from this set. One set membership, two contradictory answers.
//
// SAFE BECAUSE THE SPONSOR IS STILL SERVER-DERIVED, which is the property that
// actually matters here. Both slugs already resolve through LOCAL_SPONSOR in
// identity.js (joe-local -> joe, dell-local -> dell) and already classify as
// sponsored_agent. Nothing this Mac asserts picks the sponsor: the mapping is
// server-side, one slug per partner deliberately so a shared credential can
// never make Dell's automation write as Joe. Adding a slug here carries the
// same design weight as adding to LOCAL_SPONSOR or DISPLAY — never routine.
const PARTNER_AUTHORITY_AGENTS = new Set(["codex", "claude", "joe-local", "dell-local"]);
const PARTNER_SLUGS = new Set(["joe", "dell"]);

export function partnerAuthoritySlugForActor(actor) {
  if (actor?.human === true && PARTNER_SLUGS.has(actor.slug)) return actor.slug;
  if (actor?.human === false && PARTNER_AUTHORITY_AGENTS.has(actor.slug) &&
      actor.native_agent_verified === true &&
      PARTNER_SLUGS.has(actor.sponsoring_human_slug) &&
      authorizationClassForActor(actor) === "sponsored_agent")
    return actor.sponsoring_human_slug;
  return null;
}

export function canExercisePartnerAuthority(actor) {
  return partnerAuthoritySlugForActor(actor) !== null;
}
