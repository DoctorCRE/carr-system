-- Persist client suggestions separately from partner business records.
-- rollback: remove consumers before retiring functions; retain proposal history.
-- lock-review: new empty tables; operations serialize one portal, then its publication.
create table ops.portal_proposal_membership (
 publication_id uuid not null references ops.client_publication(id),
 publication_version integer not null check(publication_version>0),
 property_ids uuid[] not null check(cardinality(property_ids)<=100),
 primary key(publication_id,publication_version)
);
create table ops.portal_proposal_version (
 portal_slug text not null references ops.client_portal(slug),
 publication_id uuid not null,
 publication_version integer not null,
 deal_id uuid not null references public.deal(id),
 version integer not null check(version>0),
 request_key text not null check(length(request_key) between 1 and 200),
 request jsonb not null,
 receipt jsonb not null,
 created_at timestamptz not null default clock_timestamp(),
 primary key(portal_slug,publication_id,version),
 unique(portal_slug,request_key),
 foreign key(publication_id,publication_version) references ops.portal_proposal_membership(publication_id,publication_version)
);
revoke all on ops.portal_proposal_membership,ops.portal_proposal_version from public,carr_reader,carr_writer,carr_authority,dot_reader;

-- Trusted publication producer only. A revision's canonical identities are immutable.
create function ops.bind_portal_proposal_publication(p_publication uuid,p_version integer,p_properties uuid[]) returns jsonb
language plpgsql volatile security definer set search_path=pg_catalog,pg_temp as $$
declare pub ops.client_publication%rowtype; prior uuid[];
begin
 perform 1 from ops.client_portal p join ops.client_publication x on x.portal_slug=p.slug
 where x.id=p_publication and p.disabled_at is null for update of p;
 if not found then raise exception 'publication_unavailable'; end if;
 select * into pub from ops.client_publication where id=p_publication for share;
 if pub.id is null or pub.kind<>'tour' or not pub.published or pub.version<>p_version then raise exception 'publication_unavailable'; end if;
 if p_properties is null or cardinality(p_properties)>100 or array_ndims(p_properties)>1
 or exists(select 1 from unnest(p_properties) u(property_id) where u.property_id is null or not exists(
 select 1 from ops.tour_property t where t.organization_tenant_id='carr-internal' and t.id=u.property_id))
 or (select count(distinct id) from unnest(p_properties) id)<>cardinality(p_properties) then raise exception 'publication_properties_invalid'; end if;
 select property_ids into prior from ops.portal_proposal_membership where publication_id=p_publication and publication_version=p_version;
 if found and prior is distinct from p_properties then raise exception 'publication_binding_conflict'; end if;
 insert into ops.portal_proposal_membership values(p_publication,p_version,p_properties) on conflict do nothing;
 return jsonb_build_object('publication_id',p_publication,'publication_version',p_version,'property_ids',p_properties);
end $$;
revoke all on function ops.bind_portal_proposal_publication(uuid,integer,uuid[]) from public,carr_reader,carr_writer,carr_authority,dot_reader;
grant execute on function ops.bind_portal_proposal_publication(uuid,integer,uuid[]) to carr_writer;

-- One transaction performs live session authorization, membership validation and CAS.
create function ops.portal_proposal(p_slug text,p_digest text,p_publication uuid,p_publication_version integer,p_request jsonb) returns jsonb
language plpgsql volatile security definer set search_path=pg_catalog,pg_temp as $$
declare portal ops.client_portal%rowtype; pub ops.client_publication%rowtype; members uuid[];
 prior ops.portal_proposal_version%rowtype; replay ops.portal_proposal_version%rowtype;
 result jsonb; selection jsonb; selected jsonb; notes jsonb; base integer; retired jsonb;
begin
 select * into portal from ops.client_portal where slug=p_slug for update;
 if portal.slug is null or (ops.read_client_portal_session(p_slug,p_digest)->>'status') is distinct from 'success' then
 return '{"status":"denied","error":"portal_access_denied"}'::jsonb; end if;
 select * into pub from ops.client_publication where id=p_publication and portal_slug=portal.slug for share;
 if pub.id is null or not pub.published or pub.kind<>'tour' then
 return '{"status":"conflict","error":"publication_unavailable"}'::jsonb; end if;
 select * into prior from ops.portal_proposal_version where portal_slug=portal.slug and publication_id=pub.id order by version desc limit 1;
 if p_request is not null then
 if jsonb_typeof(p_request)<>'object' then return '{"status":"invalid","error":"invalid_proposal"}'::jsonb; end if;
 if octet_length(p_request::text)>262144
 or (select array_agg(k order by k) from jsonb_object_keys(p_request) k) is distinct from array['idempotency_key','notes','selected','version']
 or jsonb_typeof(p_request->'idempotency_key')<>'string' or length(p_request->>'idempotency_key') not between 1 and 200
 or jsonb_typeof(p_request->'version')<>'number' or (p_request->>'version') !~ '^(0|[1-9][0-9]{0,8})$'
 or jsonb_typeof(p_request->'selected')<>'array' or jsonb_typeof(p_request->'notes')<>'object' then
 return '{"status":"invalid","error":"invalid_proposal"}'::jsonb; end if;
 select * into replay from ops.portal_proposal_version where portal_slug=portal.slug and request_key=p_request->>'idempotency_key';
 if found then
 if replay.publication_id=pub.id and replay.publication_version=p_publication_version and replay.request=p_request then return replay.receipt; end if;
 return '{"status":"conflict","error":"idempotency_conflict"}'::jsonb; end if;
 end if;
 select property_ids into members from ops.portal_proposal_membership where publication_id=pub.id and publication_version=pub.version;
 if members is null or (p_request is not null and pub.version is distinct from p_publication_version) then
 return '{"status":"conflict","error":"publication_unavailable"}'::jsonb; end if;
 if p_request is null then
 selection:=coalesce(prior.receipt->'selection','{"version":0,"selected":[],"notes":{}}'::jsonb);
 select coalesce(jsonb_agg(id),'[]'::jsonb) into retired from jsonb_array_elements_text(selection->'selected') id where not id::uuid=any(members);
 return jsonb_build_object('status','success','schema_version','portal-proposals.v1','publication_id',pub.id,
 'publication_version',pub.version,'selection',selection,'retired',retired);
 end if;
 base:=(p_request->>'version')::integer;
 if base>=10000 then return '{"status":"invalid","error":"proposal_capacity"}'::jsonb; end if;
 if base<>coalesce(prior.version,0) then return '{"status":"conflict","error":"version_conflict"}'::jsonb; end if;
 selected:=p_request->'selected'; notes:=p_request->'notes';
 if jsonb_array_length(selected)>100 or exists(select 1 from jsonb_array_elements(selected) id
 where jsonb_typeof(id)<>'string' or (id#>>'{}') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
 or (select count(distinct id) from jsonb_array_elements_text(selected) id)<>jsonb_array_length(selected) then
 return '{"status":"invalid","error":"invalid_proposal"}'::jsonb; end if;
 if exists(select 1 from jsonb_array_elements_text(selected) id where not id::uuid=any(members)) then
 return '{"status":"conflict","error":"property_not_published"}'::jsonb; end if;
 if exists(select 1 from jsonb_each(notes) n where not selected ? n.key or jsonb_typeof(n.value)<>'string' or length(n.value#>>'{}')>2000) then
 return '{"status":"invalid","error":"invalid_proposal"}'::jsonb; end if;
 selection:=jsonb_build_object('version',base+1,'selected',selected,'notes',notes);
 result:=jsonb_build_object('status','success','schema_version','portal-proposals.v1','publication_id',pub.id,
 'publication_version',pub.version,'selection',selection,'retired','[]'::jsonb);
 insert into ops.portal_proposal_version(portal_slug,publication_id,publication_version,deal_id,version,request_key,request,receipt)
 values(portal.slug,pub.id,pub.version,portal.deal_id,base+1,p_request->>'idempotency_key',p_request,result);
 return result;
end $$;
revoke all on function ops.portal_proposal(text,text,uuid,integer,jsonb) from public,carr_reader,carr_writer,carr_authority,dot_reader;
grant execute on function ops.portal_proposal(text,text,uuid,integer,jsonb) to carr_writer;

-- Partner consumers authorize the deal before this exact-version read, like list_client_publications.
create function ops.read_portal_proposal_for_deal(p_deal uuid,p_publication uuid,p_version integer) returns jsonb
language sql stable security definer set search_path=pg_catalog,pg_temp as $$
 select v.receipt from ops.portal_proposal_version v join ops.client_portal p on p.slug=v.portal_slug and p.deal_id=v.deal_id
 where v.deal_id=p_deal and v.publication_id=p_publication and (p_version is null or v.version=p_version)
 order by v.version desc limit 1
$$;
revoke all on function ops.read_portal_proposal_for_deal(uuid,uuid,integer) from public,carr_reader,carr_writer,carr_authority,dot_reader;
grant execute on function ops.read_portal_proposal_for_deal(uuid,uuid,integer) to carr_reader,carr_writer;

do $contract$
declare contract jsonb:=ops.scac_current_grant_contract(); item record;
begin
 for item in select * from (values
 ('ops.bind_portal_proposal_publication(p_publication uuid, p_version integer, p_properties uuid[])','carr_writer','v'),
 ('ops.portal_proposal(p_slug text, p_digest text, p_publication uuid, p_publication_version integer, p_request jsonb)','carr_writer','v'),
 ('ops.read_portal_proposal_for_deal(p_deal uuid, p_publication uuid, p_version integer)','carr_reader','s'),
 ('ops.read_portal_proposal_for_deal(p_deal uuid, p_publication uuid, p_version integer)','carr_writer','s')) as x(signature,grantee,volatility)
 loop
 contract:=jsonb_set(contract,'{secdef_execute}',contract->'secdef_execute'||jsonb_build_array(jsonb_build_object(
 'config',jsonb_build_array('search_path=pg_catalog, pg_temp'),'grantee',item.grantee,'parallel','u','grantable',false,'privilege','execute',
 'signature',item.signature,'volatility',item.volatility,'ingress_key','db-function-acl:'||item.signature||':'||item.grantee||':execute',
 'ingress_kind','db_function_acl','function_kind','f','security_definer',true)));
 end loop;
 execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $contract$;
