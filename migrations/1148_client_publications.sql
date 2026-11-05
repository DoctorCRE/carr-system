-- rollback: forward-only; retain authored publications, remove callers before retirement.
-- lock-review: new empty table; reader replacement does not rewrite portal records.
create table ops.client_publication (
 id uuid primary key,
 portal_slug text not null references ops.client_portal(slug),
 kind text not null check(kind in ('milestone','document','tour','report','deliverable')),
 title text not null check(length(btrim(title)) between 1 and 200),
 content text,
 deliverable_id uuid references ops.deal_deliverable(id),
 deliverable_version integer,
 published boolean not null,
 version integer not null check(version>0),
 updated_by text not null,
 updated_at timestamptz not null default now(),
 check((kind='deliverable' and deliverable_id is not null and deliverable_version is not null and content is null)
   or (kind<>'deliverable' and deliverable_id is null and deliverable_version is null and content is not null and length(btrim(content)) between 1 and 10000))
);
create index client_publication_portal on ops.client_publication(portal_slug,id);
revoke all on ops.client_publication from public,carr_reader,carr_writer,dot_reader;

create function ops.client_portal_projection(p_slug text) returns jsonb
language sql stable security definer set search_path=pg_catalog,pg_temp as $$
 select jsonb_build_object('slug',p.slug,
 'roadmap',jsonb_build_object('current_phase',null,'phases','[]'::jsonb),
 'materials',(select coalesce(jsonb_agg(jsonb_build_object('kind',m.kind,'title',m.title,'content',m.content) order by m.shared_at,m.id),'[]'::jsonb)
   from ops.client_portal_material m where m.portal_slug=p.slug and m.shared_at<=now() and m.withdrawn_at is null),
 'publications',(select coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object('id',x.id,'kind',x.kind,'title',x.title,'content',x.content,
   'deliverable',case when f.id is not null then jsonb_build_object('id',f.id,'filename',f.filename,'mime',f.mime,'bytes',f.bytes) end)) order by x.updated_at,x.id),'[]'::jsonb)
   from ops.client_publication x left join ops.deal_deliverable f on f.id=x.deliverable_id and f.deal_id=p.deal_id
     and f.organization_tenant_id='carr-internal' and f.state='finalized' and f.visibility='shared' and f.version=x.deliverable_version
   where x.portal_slug=p.slug and x.published and (x.kind<>'deliverable' or f.id is not null)))
 from ops.client_portal p join public.deal d on d.id=p.deal_id and d.client_id=p.client_id
 join public.client c on c.id=p.client_id and c.merged_into is null
 join public.party party on party.id=c.party_id and party.merged_into is null and party.deleted_at is null
 where p.slug=p_slug and p.disabled_at is null
 and (select count(*) from ops.client_portal_material m where m.portal_slug=p.slug and m.shared_at<=now() and m.withdrawn_at is null)<=100
$$;
revoke all on function ops.client_portal_projection(text) from public;
-- Partner preview only. Public consumers must use the password reader.
grant execute on function ops.client_portal_projection(text) to carr_reader,carr_writer;

create function ops.write_client_publication(p_deal uuid,p_id uuid,p_version integer,p_published boolean,
 p_kind text,p_title text,p_content text,p_file uuid,p_actor text) returns jsonb
language plpgsql volatile security definer set search_path=pg_catalog,pg_temp as $$
declare portal ops.client_portal%rowtype; existing ops.client_publication%rowtype; file_version integer; result jsonb;
begin
 perform pg_advisory_xact_lock(hashtextextended('publication:'||p_id::text,0));
 select p.* into portal from ops.client_portal p join public.deal d on d.id=p.deal_id and d.client_id=p.client_id
 join public.client c on c.id=p.client_id and c.merged_into is null
 join public.party party on party.id=c.party_id and party.merged_into is null and party.deleted_at is null
 where p.deal_id=p_deal and p.disabled_at is null for update of p;
 if portal.slug is null then raise exception 'publication_portal_unavailable'; end if;
 if p_actor is null or p_actor not in ('joe','dell') or not exists(select 1 from public.actor where slug=p_actor and active) then raise exception 'publication_denied'; end if;
 select * into existing from ops.client_publication where id=p_id;
 if existing.id is not null and existing.portal_slug<>portal.slug then raise exception 'publication_not_found'; end if;
 if p_version is distinct from coalesce(existing.version,0) then raise exception 'publication_version_conflict'; end if;
 if p_kind='deliverable' then
   select version into file_version from ops.deal_deliverable where id=p_file and deal_id=p_deal
     and organization_tenant_id='carr-internal' and state='finalized' and (visibility='shared' or not p_published) for share;
   if file_version is null then raise exception 'publication_file_not_shared'; end if;
 end if;
 if existing.id is null and (select count(*) from ops.client_publication where portal_slug=portal.slug)>=1000 then raise exception 'publication_capacity'; end if;
 if p_published and (select count(*) from ops.client_publication where portal_slug=portal.slug and published and id<>p_id)>=100 then raise exception 'publication_capacity'; end if;
 insert into ops.client_publication(id,portal_slug,kind,title,content,deliverable_id,deliverable_version,published,version,updated_by)
 values(p_id,portal.slug,p_kind,p_title,p_content,p_file,file_version,p_published,p_version+1,p_actor)
 on conflict(id) do update set kind=excluded.kind,title=excluded.title,content=excluded.content,deliverable_id=excluded.deliverable_id,
 deliverable_version=excluded.deliverable_version,published=excluded.published,version=excluded.version,updated_by=excluded.updated_by,updated_at=clock_timestamp()
 returning jsonb_build_object('id',id,'version',version,'published',published) into result;
 return result;
end $$;
revoke all on function ops.write_client_publication(uuid,uuid,integer,boolean,text,text,text,uuid,text) from public;
grant execute on function ops.write_client_publication(uuid,uuid,integer,boolean,text,text,text,uuid,text) to carr_writer;

create function ops.list_client_publications(p_deal uuid) returns jsonb
language sql stable security definer set search_path=pg_catalog,pg_temp as $$
 select coalesce(jsonb_agg(jsonb_build_object('id',x.id,'kind',x.kind,'title',x.title,'content',x.content,
 'deliverable_id',x.deliverable_id,'published',x.published,'version',x.version) order by x.updated_at,x.id),'[]'::jsonb)
 from ops.client_publication x join ops.client_portal p on p.slug=x.portal_slug and p.disabled_at is null
 where p.deal_id=p_deal
$$;
revoke all on function ops.list_client_publications(uuid) from public;
grant execute on function ops.list_client_publications(uuid) to carr_reader,carr_writer;

create or replace function ops.read_client_portal(p_slug text,p_password text) returns jsonb
language plpgsql volatile security definer set search_path=pg_catalog,pg_temp as $$
declare
  portal ops.client_portal%rowtype;
  observed_hash text;
  supplied_hash text;
  result jsonb;
  attempt_time timestamptz;
begin
  if p_slug is null or p_slug !~ '^[a-z0-9][a-z0-9-]{0,62}$'
     or p_password is null or length(p_password) not between 1 and 1024 then
    return null;
  end if;
  select p.password_hash into observed_hash from ops.client_portal p where p.slug=p_slug;
  -- Bcrypt must not serialize callers of a real slug while unknown slugs run in parallel.
  supplied_hash := public.crypt(encode(public.digest(p_password,'sha256'),'hex'),
    coalesce(observed_hash,'$2a$12$......................0000000000000000000000000000000'));
  select p.* into portal from ops.client_portal p where p.slug=p_slug for update;
  attempt_time := clock_timestamp();
  if portal.attempt_window_started_at is null
     or portal.attempt_window_started_at <= attempt_time - interval '15 minutes' then
    portal.failed_attempts := 0;
    portal.attempt_window_started_at := attempt_time;
  end if;
  if portal.failed_attempts >= 5 then
    return null;
  end if;
  if portal.slug is null or portal.password_hash is distinct from observed_hash then
    return null;
  end if;
  if portal.password_hash is distinct from supplied_hash then
    update ops.client_portal set failed_attempts=portal.failed_attempts+1,
      attempt_window_started_at=portal.attempt_window_started_at where slug=portal.slug;
    return null;
  end if;
  update ops.client_portal set failed_attempts=0,attempt_window_started_at=null where slug=portal.slug;
  if portal.disabled_at is not null then
    return null;
  end if;
  result := ops.client_portal_projection(portal.slug);
  return result;
end $$;

do $contract$
declare contract jsonb := ops.scac_current_grant_contract(); item record;
begin
 for item in select * from (values
 ('ops.list_client_publications(p_deal uuid)','carr_reader','s'),
 ('ops.list_client_publications(p_deal uuid)','carr_writer','s'),
 ('ops.client_portal_projection(p_slug text)','carr_reader','s'),
 ('ops.client_portal_projection(p_slug text)','carr_writer','s'),
 ('ops.write_client_publication(p_deal uuid, p_id uuid, p_version integer, p_published boolean, p_kind text, p_title text, p_content text, p_file uuid, p_actor text)','carr_writer','v')) as x(signature,grantee,volatility)
 loop
 contract := jsonb_set(contract,'{secdef_execute}',contract->'secdef_execute'||jsonb_build_array(jsonb_build_object(
 'config',jsonb_build_array('search_path=pg_catalog, pg_temp'),'grantee',item.grantee,'parallel','u','grantable',false,'privilege','execute',
 'signature',item.signature,'volatility',item.volatility,'ingress_key','db-function-acl:'||item.signature||':'||item.grantee||':execute',
 'ingress_kind','db_function_acl','function_kind','f','security_definer',true)));
 end loop;
 execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $contract$;
