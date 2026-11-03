// Install transaction-local decisions from the ordinary canonical deal policy.
import { evaluateActorAuthority } from './global-boundaries.v5.js';
import { organizationTenantForActor } from './identity.js';

// Bind the existing deal-policy decision to this transaction and deal revision.
export async function installTourDealAccess(client, actor) {
  const tenant = organizationTenantForActor(actor);
  const deals = {};
  if (tenant === 'carr-internal') {
    const result = await client.query(`select d.id, d.owner, d.version
      from public.deal d join public.client c on c.id=d.client_id
      join public.party p on p.id=c.party_id
      where c.merged_into is null and p.merged_into is null and p.deleted_at is null
        and exists(select 1 from public.actor a where a.slug=$1 and a.active)`, [actor.slug]);
    for (const deal of result.rows) {
      if (!deal.owner) continue;
      const decision = evaluateActorAuthority({actor, action:'business.read_deal', tenant,
        now:new Date().toISOString(), controls:{deal_owner_slug:deal.owner,
          policy_scope:['business.read_deal'], capabilities:['deal.read']}});
      if (decision.decision === 'allow') deals[deal.id] = {owner:deal.owner, version:deal.version};
    }
  }
  await client.query("select set_config('carr.tour_deal_access',$1,true)",
    [JSON.stringify({slug:actor.slug, id:actor.id || actor.slug, deals})]);
}
