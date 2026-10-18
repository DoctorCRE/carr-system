// One exact snapshot: graph, recorded referral outcomes and offered introductions.
// No name matching, inferred employment edges, or vendor score probabilities.
export const NETWORK_SCHEMA = 'carr-relationship-network.v1';
export const REFERRAL_KINDS = ['referral', 'referred'];
// The one exact party-to-deal association rule; the network snapshot and the
// vendor directory both count from it. Sources: evidence on every live vendor
// row for a party, and referral deals attached to a relationship, credited to
// the referrer accepted when that deal was attached. Live client deals only.
// A date is UTC midnight, as vendor evidence stores one, whatever the session zone.
export const exactDealAssociations = `select e.party_id,e.deal_id,e.role,e.occurred_at,e.detail,d.outcome from (
 select v.party_id,x.deal_id,x.role,x.occurred_at,x.evidence_ref detail
 from public.vendor v cross join lateral jsonb_to_recordset(coalesce(v.deal_evidence,'[]'::jsonb)) x(deal_id uuid,role text,occurred_at timestamptz,evidence_ref text)
 where v.merged_into is null
 union all
 select r.referred_by,r.deal_id,'referred',coalesce(r.occurred_on::timestamp at time zone 'UTC',r.created_at),r.note from public.party_link_deal r
) e join public.deal d on d.id=e.deal_id join public.client dc on dc.id=d.client_id and dc.merged_into is null
 join public.party dp on dp.id=dc.party_id and dp.merged_into is null and dp.deleted_at is null`;
export const networkStatement = `with live as (
 select p.id,p.name,p.city,p.state,p.contact_state,p.contact_state_until,
   -- Any role row's restriction holds the party; the display row is not the authority.
   (exists(select 1 from public.lead rl where rl.party_id=p.id and (coalesce(rl.suppressed,false) or rl.stage='do_not_contact'))
    or exists(select 1 from public.vendor rv where rv.party_id=p.id and rv.merged_into is null and rv.disposition='avoid')) restricted,
   v.id vendor_id,c.id client_id,l.id lead_id,
   coalesce(v.vendor_ref,c.roster_ref,l.registry_ref,p.ref) ref,
   case when v.id is not null then 'vendor' when c.id is not null then 'client' when l.id is not null then 'lead' else 'contact' end kind,
   coalesce(v.territory,p.city) territory,
   coalesce(v.verticals,case when c.vertical is not null then array[c.vertical] when l.segment is not null then array[l.segment] else array[]::text[] end) verticals,
   a.slug owner,
   coalesce(v.offers,c.notes,l.notes) summary
 from public.party p
 left join lateral (select id,vendor_ref,territory,verticals,owner_id,offers from public.vendor v where v.party_id=p.id and v.merged_into is null order by v.id limit 1) v on true
 left join lateral (select id,roster_ref,vertical,owner_id,notes from public.client c where c.party_id=p.id and c.merged_into is null order by c.id limit 1) c on true
 left join lateral (select id,registry_ref,segment,owner_id,notes from public.lead l where l.party_id=p.id order by l.id limit 1) l on true
 left join public.actor a on a.id=coalesce(v.owner_id,c.owner_id,l.owner_id)
 where p.merged_into is null and p.deleted_at is null
), links as (
 select l.id,l.from_party,l.to_party,l.via_party,l.kind,l.occurred_on,l.created_at,l.note from public.party_link l join live f on f.id=l.from_party join live t on t.id=l.to_party
 left join live v on v.id=l.via_party where (l.via_party is null or v.id is not null) and l.kind in ('knows','works_with','can_introduce','intro_requested','introduced','intro','intro_received','referral','referred')
), deals as (
 select d.id,d.name,d.outcome,d.phase,d.city,c.party_id,c.vertical,d.owner
 from public.deal d join public.client c on c.id=d.client_id and c.merged_into is null join live p on p.id=c.party_id
), associations as (
 select a.party_id,a.deal_id,a.role,a.occurred_at,a.detail from (${exactDealAssociations}) a join live p on p.id=a.party_id
), nodes as (
 select jsonb_build_object('id','party:'||p.id,'record_id',coalesce(p.vendor_id,p.client_id,p.lead_id,p.id),'ref',p.ref,'name',p.name,'kind',p.kind,
 'territory',p.territory,'verticals',p.verticals,'owner',p.owner,'summary',p.summary,'contact_state',p.contact_state,'contact_state_until',p.contact_state_until,'restricted',p.restricted) item
 from live p where p.vendor_id is not null or p.client_id is not null or p.lead_id is not null or exists(select 1 from links l where p.id in (l.from_party,l.to_party,l.via_party))
 union all
 select jsonb_build_object('id','deal:'||d.id,'record_id',d.id,'name',d.name,'kind','deal','territory',d.city,'verticals',case when d.vertical is null then array[]::text[] else array[d.vertical] end,'owner',d.owner,'summary',d.phase,'outcome',d.outcome,'contact_state','active') from deals d
), edges as (
 select jsonb_build_object('id','link:'||l.id,'from','party:'||l.from_party,'to','party:'||l.to_party,'via',case when l.via_party is null then null else 'party:'||l.via_party end,'kind',l.kind,'when',coalesce(l.occurred_on::timestamptz,l.created_at),'summary',left(coalesce(l.note,''),180),'detail',l.note) item from links l
 union all
 select jsonb_build_object('id','client-deal:'||d.id,'from','party:'||d.party_id,'to','deal:'||d.id,'via',null,'kind','client_deal','when',null,'summary',d.phase,'detail',null) from deals d
 union all
 select jsonb_build_object('id','evidence:'||a.party_id||':'||a.deal_id||':'||a.role,'from','party:'||a.party_id,'to','deal:'||a.deal_id,'via',null,'kind',a.role,'when',min(a.occurred_at),'summary',a.role,'detail',string_agg(distinct a.detail,E'\n')) from associations a group by a.party_id,a.deal_id,a.role
)
select jsonb_build_object('nodes',coalesce((select jsonb_agg(item order by item->>'name',item->>'id') from nodes),'[]'::jsonb),
 'edges',coalesce((select jsonb_agg(item order by item->>'id') from edges),'[]'::jsonb),
 'referrals',coalesce((select jsonb_agg(item order by item->>'node_id') from (
 select jsonb_build_object('node_id','party:'||p.id,'deals',count(distinct a.deal_id),'won',count(distinct a.deal_id) filter(where d.outcome='won'),'lost',count(distinct a.deal_id) filter(where d.outcome='lost')) item
 from live p join associations a on a.party_id=p.id and a.role='referred' join deals d on d.id=a.deal_id where p.kind in ('vendor','client') group by p.id
 ) r),'[]'::jsonb)) snapshot`;

function held(node, now) {
  if (node?.restricted) return true;
  return node?.contact_state && node.contact_state !== 'active'
    && (!node.contact_state_until || node.contact_state_until >= now.slice(0,10));
}
export function projectNetwork(snapshot, observedAt) {
  if (!Array.isArray(snapshot?.nodes) || !Array.isArray(snapshot?.edges) || !Array.isArray(snapshot?.referrals)) throw Object.assign(new Error('INTERNAL_ERROR'), {code:'INTERNAL_ERROR'});
  // Refuse oversize snapshots, never silently call a clipped graph complete.
  if (snapshot.nodes.length > 5000 || snapshot.edges.length > 20000) throw Object.assign(new Error('DEPENDENCY_UNAVAILABLE'), {code:'DEPENDENCY_UNAVAILABLE'});
  const nodes = new Map(snapshot.nodes.map(node => [node.id,node]));
  const suggestions = snapshot.edges.filter(edge => edge.kind === 'can_introduce' && edge.detail?.trim()
    && nodes.has(edge.from) && nodes.has(edge.to) && (!edge.via || nodes.has(edge.via))
    && [edge.from,edge.to,edge.via].filter(Boolean).every(id=>!held(nodes.get(id),observedAt))
    // Endpoint order records direction, not whether the two have been connected.
    && !snapshot.edges.some(other=> ['introduced','intro','intro_received','intro_requested'].includes(other.kind)
      && [other.from,other.to].sort().join() === [edge.from,edge.to].sort().join()))
    .sort((a,b)=>String(b.when||'').localeCompare(String(a.when||'')) || a.id.localeCompare(b.id))
    .map(edge=>({id:edge.id,from:edge.from,to:edge.to,via:edge.via,reason:edge.summary || edge.detail.slice(0,180),detail:edge.detail,when:edge.when}));
  return {schema:NETWORK_SCHEMA,observed_at:observedAt,valid_until:new Date(Date.parse(observedAt)+60000).toISOString(),...snapshot,
    referrals:snapshot.referrals.map(row=>({...row,win_rate:row.won+row.lost ? row.won/(row.won+row.lost):null})),suggestions};
}

// Add exact deal attribution to an existing relationship without replacing it.
// The row keeps the referrer the caller resolved from the locked relationship
// and the caller's date (else the relationship's), so later backfills on the
// shared relationship never move an already recorded deal.
export async function bindReferralDeal(client, actor, args, ends, kind, link) {
  if (args.deal_id == null) return null;
  if (!REFERRAL_KINDS.includes(kind) || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(args.deal_id) || typeof args.note !== 'string' || !args.note.trim()) throw Object.assign(new Error('referral_deal_invalid'),{code:'referral_deal_invalid'});
  const found = await client.query(`select d.id from public.deal d join public.client c on c.id=d.client_id and c.merged_into is null join public.party p on p.id=c.party_id and p.merged_into is null and p.deleted_at is null where d.id=$1::uuid and p.id=$2::uuid`,[args.deal_id,ends.to_party]);
  if (!found.rows.length) throw Object.assign(new Error('referral_deal_target_mismatch'),{code:'referral_deal_target_mismatch'});
  // Coverage spans every live vendor row of the referrer, including rows that
  // do not exist yet. FOR UPDATE on the party conflicts with the KEY SHARE lock
  // a vendor insert's (or re-point's) foreign-key check takes, so no role row
  // can appear between this lookup and commit; one created first is seen here.
  await client.query(`select 1 from public.party where id=$1 for update`,[link.referred_by]);
  // update-vendor's verification locks the same rows via versionGuard. Lock in
  // UUID order (as vendor merges do), then clear coverage with the insertion.
  // Transaction/statement timestamps cannot order concurrent commits. A stale
  // verification waits and fails its version check; a prior one is invalidated.
  const vendors = await client.query(`select id from public.vendor where party_id=$1 and merged_into is null order by id for update`,[link.referred_by]);
  const result = await client.query(`insert into public.party_link_deal(link_id,deal_id,created_by,note,referred_by,occurred_on)
    values($1,$2,$3,$4,$5,coalesce($6::date,(select occurred_on from public.party_link where id=$1))) on conflict do nothing returning deal_id,referred_by`,
    [link.id,args.deal_id,actor.id,args.note.trim(),link.referred_by,link.occurred_on]);
  if (result.rows.length && vendors.rows.length) await client.query(`update public.vendor set deal_history_verified_at=null,updated_by=$2 where id=any($1::uuid[])`,[vendors.rows.map(v=>v.id),actor.id]);
  return result.rows[0] || null;
}
