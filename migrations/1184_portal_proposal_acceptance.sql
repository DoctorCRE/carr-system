-- Partner acceptance shares the client-save serialization boundary.
-- rollback: retire consumers first; retain immutable cart and event history.
-- lock-review: portal, publication, then Tour; no table rewrite or table grants.
create function ops.review_portal_proposal(p_deal uuid,p_publication uuid,p_tour uuid,p_request jsonb) returns jsonb
language plpgsql volatile security definer set search_path=pg_catalog,pg_temp as $$
declare portal ops.client_portal%rowtype; pub ops.client_publication%rowtype;
 source ops.portal_proposal_version%rowtype; cart ops.tour_selection_cart_version%rowtype;
 members uuid[]; result jsonb; selected jsonb; added jsonb; removed jsonb; retained jsonb; retired jsonb; new_id uuid;
 actor_slug text:=current_setting('carr.acting_actor_slug',true);
begin
 if actor_slug not in ('joe','dell') or actor_slug is null
 or current_setting('carr.verified_human_actor_slug',true) is distinct from actor_slug
 or current_setting('carr.organization_tenant_id',true) is distinct from 'carr-internal'
 or not coalesce(ops.tour_deal_visible('carr-internal',p_deal,actor_slug),false)
 then raise exception 'proposal_access_denied'; end if;
 select p.* into portal from ops.client_portal p join ops.client_publication x on x.portal_slug=p.slug
 where x.id=p_publication and p.deal_id=p_deal for update of p;
 if portal.slug is null or portal.disabled_at is not null or not exists(
 select 1 from public.deal d join public.client c on c.id=d.client_id join public.party y on y.id=c.party_id
 where d.id=p_deal and d.client_id=portal.client_id and c.merged_into is null and y.merged_into is null and y.deleted_at is null)
 then raise exception 'proposal_retired'; end if;
 select * into pub from ops.client_publication where id=p_publication and portal_slug=portal.slug for share;
 if pub.id is null or not pub.published or pub.kind<>'tour' then raise exception 'proposal_retired'; end if;
 select property_ids into members from ops.portal_proposal_membership where publication_id=pub.id and publication_version=pub.version;
 if members is null then raise exception 'proposal_retired'; end if;
 perform 1 from ops.tour where organization_tenant_id='carr-internal' and id=p_tour and deal_id=p_deal for update;
 if not found then raise exception 'proposal_tour_unavailable'; end if;
 select * into source from ops.portal_proposal_version where portal_slug=portal.slug and publication_id=pub.id and deal_id=p_deal order by version desc limit 1;
 if source.version is null then raise exception 'proposal_not_found'; end if;
 select * into cart from ops.tour_selection_cart_version where organization_tenant_id='carr-internal' and tour_id=p_tour order by selection_version desc,id desc limit 1;
 selected:=source.receipt#>'{selection,selected}';
 select coalesce(jsonb_agg(id order by pos),'[]') into retired from jsonb_array_elements_text(selected) with ordinality u(id,pos)
 where not id::uuid=any(members) or not exists(select 1 from ops.tour_property t where t.organization_tenant_id='carr-internal' and t.id=u.id::uuid);
 select coalesce(jsonb_agg(id order by pos),'[]') into added from jsonb_array_elements_text(selected) with ordinality u(id,pos) where not coalesce(cart.property_ids,'[]') ? id and not retired ? id;
 select coalesce(jsonb_agg(id order by pos),'[]') into retained from jsonb_array_elements_text(selected) with ordinality u(id,pos) where coalesce(cart.property_ids,'[]') ? id and not retired ? id;
 select coalesce(jsonb_agg(id order by pos),'[]') into removed from jsonb_array_elements_text(coalesce(cart.property_ids,'[]')) with ordinality u(id,pos) where not selected ? id;
 result:=jsonb_build_object('schema_version','portal-proposal-acceptance.v1','deal_id',p_deal,'tour_id',p_tour,'publication_id',pub.id,
 'publication_version',pub.version,'source_version',source.version,'cart_version',coalesce(cart.selection_version,0),'cart_version_id',cart.id,
 'selected',selected,'mine',coalesce(cart.property_ids,'[]'),'notes',source.receipt#>'{selection,notes}',
 'added',added,'removed',removed,'retained',retained,'retired',retired,'state','reviewed');
 if p_request is null then return result; end if;
 if jsonb_typeof(p_request)<>'object' or
 (select array_agg(k order by k) from jsonb_object_keys(p_request) k) is distinct from array['cart_version','cart_version_id','decision','property_ids','publication_version','source_version']
 or p_request->>'decision' not in ('accept','keep') or p_request->>'decision' is null
 then raise exception 'proposal_input_invalid'; end if;
 if p_request->'source_version' is distinct from to_jsonb(source.version) or p_request->'publication_version' is distinct from to_jsonb(pub.version)
 or p_request->'cart_version' is distinct from to_jsonb(coalesce(cart.selection_version,0)) or p_request->'cart_version_id' is distinct from coalesce(to_jsonb(cart.id),'null')
 then raise exception 'proposal_source_changed'; end if;
 selected:=p_request->'property_ids';
 if selected is null or jsonb_typeof(selected)<>'array' then raise exception 'proposal_input_invalid'; end if;
 if jsonb_array_length(selected)>100 or exists(select 1 from jsonb_array_elements(selected) id where jsonb_typeof(id)<>'string')
 or (select count(distinct id) from jsonb_array_elements_text(selected) id)<>jsonb_array_length(selected)
 or exists(select 1 from jsonb_array_elements_text(selected) id where not ((result->'selected') ? id or (result->'mine') ? id) or (result->'retired') ? id)
 then raise exception 'proposal_input_invalid'; end if;
 if p_request->>'decision'='keep' then
 if selected is distinct from coalesce(cart.property_ids,'[]') then raise exception 'proposal_input_invalid'; end if;
 return result||jsonb_build_object('state','kept','property_ids',selected,'selection_version_id',cart.id);
 end if;
 new_id:=ops.append_tour_selection_cart_version('carr-internal',p_tour,cart.id,selected,coalesce(cart.selection_version,0),
 'sha256:'||encode(public.digest(convert_to(selected::text,'UTF8'),'sha256'),'hex'));
 return result||jsonb_build_object('state','accepted','property_ids',selected,'selection_version_id',new_id);
end $$;
revoke all on function ops.review_portal_proposal(uuid,uuid,uuid,jsonb) from public,carr_reader,carr_writer,carr_authority,dot_reader;
-- Only the authenticated partner writer adapter may enter this function.
grant execute on function ops.review_portal_proposal(uuid,uuid,uuid,jsonb) to carr_writer;
do $contract$
declare contract jsonb:=ops.scac_current_grant_contract(); signature text:='ops.review_portal_proposal(p_deal uuid, p_publication uuid, p_tour uuid, p_request jsonb)';
begin
 contract:=jsonb_set(contract,'{secdef_execute}',contract->'secdef_execute'||jsonb_build_array(jsonb_build_object(
 'config',jsonb_build_array('search_path=pg_catalog, pg_temp'),'grantee','carr_writer','parallel','u','grantable',false,'privilege','execute',
 'signature',signature,'volatility','v','ingress_key','db-function-acl:'||signature||':carr_writer:execute',
 'ingress_kind','db_function_acl','function_kind','f','security_definer',true)));
 execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $contract$;
