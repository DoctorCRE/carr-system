import { partnerAuthoritySlugForActor } from "./partner-authority.js";

// The caller supplies only a record reference. Owners come from locked rows;
// sponsor authority comes from the authenticated server identity. Keep legacy
// machine-owned records writable by that exact principal, without assigning
// those records to whichever human happens to sponsor a later session.
export async function isPersonalRecordOwner(client, actor, ownerId) {
  if (!ownerId) return false;
  if (actor.id && ownerId === actor.id) return true;
  const sponsor = partnerAuthoritySlugForActor(actor);
  if (!sponsor || actor.human === true) return false;
  const owner = await client.query("select id from actor where slug=$1", [sponsor]);
  return owner.rows.some(row => row.id === ownerId);
}
