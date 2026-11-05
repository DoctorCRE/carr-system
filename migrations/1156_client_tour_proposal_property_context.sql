-- rollback: forward-only; restore the prior read in a successor migration.
-- lock-review: replaces one read function; no table rewrite or data mutation.
create or replace function ops.list_client_tour_proposals(p_deal uuid,p_actor text) returns jsonb
language sql stable security definer set search_path=pg_catalog,pg_temp as $$
 with authorized as materialized (
   select p_deal deal_id where p_actor in ('joe','dell')
     and ops.tour_deal_visible('carr-internal',p_deal,p_actor)
 ), proposals as materialized (
   select p.*,cart.id cart_id,coalesce(cart.property_ids,'[]') cart_property_ids
   from authorized a join ops.client_tour_proposal p on p.deal_id=a.deal_id
   left join lateral (
     select id,property_ids from ops.tour_selection_cart_version
     where organization_tenant_id='carr-internal' and tour_id=p.tour_id
     order by selection_version desc limit 1
   ) cart on true
   order by p.pulled_at desc,p.id limit 50
 ), requested as materialized (
   select distinct value::uuid id from proposals p
   cross join lateral jsonb_array_elements_text(p.property_ids||p.cart_property_ids||p.base_property_ids)
 ), labels as materialized (
   select distinct on (a.property_id,a.field_key) a.property_id,a.field_key,a.value
   from requested r join ops.tour_property p on p.id=r.id and p.organization_tenant_id='carr-internal'
   join ops.tour_field_assertion a on a.property_id=p.id and a.organization_tenant_id=p.organization_tenant_id
   where a.field_key in ('display.name','display.address') and a.review_state='reviewed'
     and a.effective_from<=now() and (a.effective_to is null or a.effective_to>now())
   order by a.property_id,a.field_key,a.effective_from desc,a.id desc
 ), properties as (
   select r.id,jsonb_build_object('carr_property_id',r.id,
     'name',name.value#>>'{}','address',address.value#>>'{}',
     'property_status',p.property_status,'eligible',coalesce(p.property_status='active',false)) item
   from requested r left join ops.tour_property p on p.id=r.id and p.organization_tenant_id='carr-internal'
   left join labels name on name.property_id=p.id and name.field_key='display.name'
   left join labels address on address.property_id=p.id and address.field_key='display.address'
 )
 select jsonb_build_object(
 'pending_count',(select count(*) from ops.client_tour_proposal where deal_id=p_deal and state in ('pending','partially_accepted')),
 'properties',coalesce((select jsonb_agg(item order by id) from properties),'[]'),
 'proposals',coalesce((select jsonb_agg(jsonb_build_object(
   'id',p.id,'deal_id',p.deal_id,'tour_id',p.tour_id,'portal_revision',p.portal_revision,
   'state',p.state,'items',(select coalesce(jsonb_agg(jsonb_build_object('carr_property_id',value)),'[]') from jsonb_array_elements(p.property_ids)),
   'choices',p.choices,'expected_selection_version_id',p.cart_id,
   'difference',jsonb_build_object(
     'add',(select coalesce(jsonb_agg(value),'[]') from jsonb_array_elements(p.property_ids) where not p.cart_property_ids @> jsonb_build_array(value)),
     'remove',(select coalesce(jsonb_agg(value),'[]') from jsonb_array_elements(p.cart_property_ids) where not p.property_ids @> jsonb_build_array(value)))
   ) order by p.pulled_at desc,p.id) from proposals p),'[]'))
 from authorized
$$;
