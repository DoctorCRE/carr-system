-- 0427_tour_rights_projection_hardening.sql
-- Forward-only hardening of 0318.  Public projections are draft containers;
-- a complete, rights-checked fact selection is sealed atomically by one typed
-- authority command.  No application role retains raw mutation privileges.

begin;

-- 0318 made seal receipts append-only.  Silently adding a required canonical
-- digest would strand any receipt that predates this migration because there
-- is no lawful UPDATE path.  Refuse before altering anything; deployment must
-- prove this count is zero or use a separately reviewed controlled backfill.
do $$
begin
  if exists (select 1 from ops.tour_public_projection_seal_receipt) then
    raise exception '0427 requires zero pre-existing projection seal receipts';
  end if;
end $$;

alter table ops.tour_source_evidence
  add column if not exists rights_provider text,
  add column if not exists rights_policy_key text;

alter table ops.tour_public_projection_seal_receipt
  add column if not exists canonical_projection_digest text;

alter table ops.tour_public_projection_seal_receipt
  add constraint tour_projection_seal_receipt_canonical_digest_shape
  check (canonical_projection_digest is not null and canonical_projection_digest ~ '^sha256:[a-f0-9]{64}$');

-- Every decision about a provider/policy stream takes the same transaction
-- advisory lock.  This makes successor admission and current-rights checks
-- serializable even when no predecessor row exists yet.
create or replace function ops.tour_rights_provider_policy_lock(
  p_tenant text, p_provider text, p_policy_key text
) returns void language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
  perform pg_advisory_xact_lock(hashtextextended(p_tenant || chr(31) || p_provider || chr(31) || p_policy_key, 0));
end $$;

create or replace function ops.tour_rights_lineage_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare prior ops.tour_rights_receipt%rowtype;
begin
  perform ops.tour_rights_provider_policy_lock(new.organization_tenant_id,new.provider,new.policy_key);
  if new.supersedes_receipt_id is null and new.receipt_version <> 1 then
    raise exception 'rights receipt version requires supersession lineage';
  end if;
  if new.supersedes_receipt_id is not null then
    select * into prior from ops.tour_rights_receipt
      where id=new.supersedes_receipt_id and organization_tenant_id=new.organization_tenant_id for update;
    if not found or prior.policy_key <> new.policy_key or prior.provider <> new.provider
       or new.receipt_version <> prior.receipt_version + 1 then
      raise exception 'rights receipt supersession lineage is invalid';
    end if;
  end if;
  return new;
end $$;

-- Evidence carries the provider/policy/receipt triple.  An assertion may only
-- use that exact evidence receipt; it cannot substitute another same-tenant
-- rights record after collection.
create or replace function ops.tour_source_rights_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
  if new.rights_provider is null or new.rights_policy_key is null then
    raise exception 'source evidence requires exact rights provider and policy lineage';
  end if;
  perform ops.tour_rights_provider_policy_lock(new.organization_tenant_id,new.rights_provider,new.rights_policy_key);
  if not exists (
    select 1 from ops.tour_rights_receipt r
     where r.id=new.rights_receipt_id and r.organization_tenant_id=new.organization_tenant_id
       and r.provider=new.rights_provider and r.policy_key=new.rights_policy_key
       and r.status='active' and r.effective_at <= new.retrieved_at
       and (r.expires_at is null or r.expires_at > new.retrieved_at) and r.revoked_at is null
       and r.allowed_use_classes ? 'source_intake'
       and not exists (
         select 1 from ops.tour_rights_receipt newer
          where newer.organization_tenant_id=r.organization_tenant_id
            and newer.provider=r.provider and newer.policy_key=r.policy_key
            and newer.receipt_version > r.receipt_version and newer.effective_at <= new.retrieved_at
       )
  ) then
    raise exception 'rights receipt refuses source intake';
  end if;
  return new;
end $$;

create or replace function ops.tour_assertion_rights_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_provider text; v_policy_key text;
begin
  select e.rights_provider,e.rights_policy_key into v_provider,v_policy_key
    from ops.tour_source_evidence e
   where e.id=new.source_evidence_id and e.organization_tenant_id=new.organization_tenant_id;
  if not found or v_provider is null or v_policy_key is null then
    raise exception 'assertion evidence rights lineage is unavailable';
  end if;
  perform ops.tour_rights_provider_policy_lock(new.organization_tenant_id,v_provider,v_policy_key);
  if not exists (
    select 1 from ops.tour_rights_receipt r
     where r.id=new.rights_receipt_id and r.organization_tenant_id=new.organization_tenant_id
       and r.id=(select e.rights_receipt_id from ops.tour_source_evidence e
                  where e.id=new.source_evidence_id and e.organization_tenant_id=new.organization_tenant_id)
       and r.provider=v_provider and r.policy_key=v_policy_key
       and r.status='active' and r.effective_at <= new.observed_at
       and (r.expires_at is null or r.expires_at > new.observed_at) and r.revoked_at is null
       and r.allowed_use_classes ? 'canonical_fact'
       and (r.allowed_field_classes ? new.field_key or r.allowed_field_classes ? '*')
       and not exists (
         select 1 from ops.tour_rights_receipt newer
          where newer.organization_tenant_id=r.organization_tenant_id
            and newer.provider=r.provider and newer.policy_key=r.policy_key
            and newer.receipt_version > r.receipt_version and newer.effective_at <= new.observed_at
       )
  ) then
    raise exception 'rights receipt refuses asserted field/use';
  end if;
  return new;
end $$;

-- Public asset metadata may identify only a server-resolved opaque asset.  A
-- URL or origin field is deliberately not representable in this public shape.
create or replace function ops.tour_public_value_safe(p_field_key text, p_value jsonb)
returns boolean language sql immutable as $$
  select case
    when p_field_key in ('display.name','display.address') then
      jsonb_typeof(p_value) = 'string' and btrim(p_value #>> '{}') <> '' and char_length(btrim(p_value #>> '{}')) <= 360
    when p_field_key in ('suite','property_type','availability','parking','access','source_attribution','as_of','caveat') then jsonb_typeof(p_value) = 'string'
    when p_field_key in ('size','asking_economics') then
      jsonb_typeof(p_value) = 'object'
      and (p_value ? 'value' or p_value ? 'min' or p_value ? 'max')
      and not exists (
        select 1 from jsonb_each(p_value) e
         where e.key not in ('value','unit','min','max','currency','period','label')
            or (e.key in ('value','min','max') and (
                 jsonb_typeof(e.value) not in ('string','number')
                 or (jsonb_typeof(e.value)='string' and (btrim(e.value #>> '{}')='' or char_length(btrim(e.value #>> '{}'))>120))))
            or (e.key in ('unit','currency','period','label') and (
                 jsonb_typeof(e.value)<>'string'
                 or btrim(e.value #>> '{}')='' or char_length(btrim(e.value #>> '{}'))>120))
      )
      and not (
        jsonb_typeof(p_value->'min')='number' and jsonb_typeof(p_value->'max')='number'
        and (p_value->>'min')::numeric > (p_value->>'max')::numeric
      )
    when p_field_key in ('photos','floor_plan') then jsonb_typeof(p_value) = 'array' and not exists (
      select 1 from jsonb_array_elements(p_value) item
       where jsonb_typeof(item) <> 'object'
          or not (item ? 'asset_ref')
          or (item->>'asset_ref') !~ '^asset:public:[A-Za-z0-9_-]+$'
          or char_length(item->>'asset_ref') not between 29 and 269
          or exists (select 1 from jsonb_each(item) e where e.key not in ('asset_ref','alt','caption','source') or jsonb_typeof(e.value) <> 'string')
    )
    else false end;
$$;

create or replace function ops.tour_projection_creation_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
  if new.status <> 'draft' then raise exception 'projection creation requires draft status'; end if;
  perform 1 from ops.tour where id=new.tour_id and organization_tenant_id=new.organization_tenant_id for update;
  if not exists (select 1 from ops.tour_property_membership m where m.organization_tenant_id=new.organization_tenant_id and m.tour_id=new.tour_id and m.route_version=new.route_version)
     or exists (select 1 from ops.tour_property_membership m where m.organization_tenant_id=new.organization_tenant_id and m.tour_id=new.tour_id and m.route_version=new.route_version and m.selected_at > new.as_of) then
    raise exception 'projection requires a complete membership set selected by as_of';
  end if;
  return new;
end $$;

create or replace function ops.tour_publication_creation_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
  if new.publication_state <> 'draft' then raise exception 'publication creation requires draft state'; end if;
  if not exists (
    select 1 from ops.tour_public_projection p
     where p.id=new.projection_id and p.organization_tenant_id=new.organization_tenant_id
       and p.status='approved' and p.projection_digest=new.projection_digest
       and exists (select 1 from ops.tour_public_projection_seal_receipt s
                    where s.organization_tenant_id=p.organization_tenant_id and s.projection_id=p.id
                      and s.canonical_projection_digest=p.projection_digest)
  ) then raise exception 'publication projection digest is unavailable'; end if;
  return new;
end $$;

create or replace function ops.tour_projection_fact_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare p ops.tour_public_projection%rowtype; v_provider text; v_policy_key text;
begin
  if tg_op <> 'INSERT' then raise exception 'tour_public_projection_fact is append-only'; end if;
  select * into p from ops.tour_public_projection
   where id=new.projection_id and organization_tenant_id=new.organization_tenant_id for update;
  if not found then raise exception 'projection fact projection is unavailable'; end if;
  select e.rights_provider,e.rights_policy_key into v_provider,v_policy_key
    from ops.tour_field_assertion a join ops.tour_source_evidence e
      on e.id=a.source_evidence_id and e.organization_tenant_id=a.organization_tenant_id
   where a.id=new.field_assertion_id and a.organization_tenant_id=new.organization_tenant_id;
  if not found or v_provider is null or v_policy_key is null then raise exception 'projection fact evidence rights lineage is unavailable'; end if;
  perform ops.tour_rights_provider_policy_lock(new.organization_tenant_id,v_provider,v_policy_key);
  if not exists (
    select 1 from ops.tour_field_assertion a
    join ops.tour_source_evidence e on e.id=a.source_evidence_id and e.organization_tenant_id=a.organization_tenant_id
    join ops.tour_rights_receipt r on r.id=a.rights_receipt_id and r.organization_tenant_id=a.organization_tenant_id
    join ops.tour_property_membership m on m.tour_id=p.tour_id and m.property_id=new.property_id and m.organization_tenant_id=new.organization_tenant_id and m.route_version=p.route_version
    where p.status='draft' and not exists (select 1 from ops.tour_public_projection_seal_receipt seal where seal.organization_tenant_id=p.organization_tenant_id and seal.projection_id=p.id)
      and new.route_version=p.route_version and a.id=new.field_assertion_id and a.organization_tenant_id=new.organization_tenant_id
      and a.property_id=new.property_id and a.field_key=new.display_field_key and a.rights_receipt_id=e.rights_receipt_id
      and r.provider=e.rights_provider and r.policy_key=e.rights_policy_key
      and e.retrieval_status='read' and e.retrieved_at <= p.as_of
      and a.review_state='reviewed' and a.data_classification='public' and a.confidence <> 'unknown'
      and m.selected_at <= p.as_of and a.observed_at <= p.as_of
      and a.effective_from <= p.as_of and (a.effective_to is null or a.effective_to > p.as_of)
      and r.status='active' and r.effective_at <= p.as_of and (r.expires_at is null or r.expires_at > p.as_of) and r.revoked_at is null
      and r.allowed_use_classes ? 'client_public_display' and (r.allowed_field_classes ? a.field_key or r.allowed_field_classes ? '*')
      and not exists (select 1 from ops.tour_rights_receipt newer where newer.organization_tenant_id=r.organization_tenant_id and newer.provider=r.provider and newer.policy_key=r.policy_key and newer.receipt_version > r.receipt_version and newer.effective_at <= p.as_of)
      and not exists (
        select 1 from ops.tour_fact_conflict conflict
        where conflict.organization_tenant_id=a.organization_tenant_id
          and conflict.property_id=a.property_id and conflict.field_key=a.field_key
          and conflict.state <> 'superseded' and conflict.opened_at <= p.as_of
          and not exists (
            select 1 from ops.tour_conflict_resolution_receipt resolution
            where resolution.organization_tenant_id=conflict.organization_tenant_id
              and resolution.conflict_id=conflict.id
              and resolution.selected_field_assertion_id=a.id
              and resolution.resolved_at <= p.as_of
          )
      )
      and ops.tour_public_value_safe(a.field_key,a.value)
  ) then raise exception 'projection fact lacks current public assertion, rights, or safe value'; end if;
  return new;
end $$;

-- A projection can leave draft only inside the seal-receipt trigger.  The
-- nesting test refuses ordinary UPDATEs while retaining the database-computed
-- canonical digest on the projection itself.
create or replace function ops.tour_public_projection_mutation_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
  if tg_op <> 'UPDATE' or pg_trigger_depth() < 2
     or old.status <> 'draft' or new.status <> 'approved'
     or (to_jsonb(new)-array['status','projection_digest']) is distinct from (to_jsonb(old)-array['status','projection_digest'])
     or new.projection_digest <> ops.tour_canonical_projection_digest(new.organization_tenant_id,new.id) then
    raise exception 'projection mutation requires atomic approved seal';
  end if;
  return new;
end $$;

-- Cross-language canonical digest byte contract (v1):
-- SHA-256(UTF-8(lines joined by LF)) where the core lines are literal
-- public-tour-projection-digest.v1, base64(tenant UTF-8), tour_id,
-- projection_id, projection_version decimal, route_version decimal, and
-- as_of UTC milliseconds YYYY-MM-DDTHH24:MI:SS.MSZ.  One subsequent line
-- per projection fact, sorted by property UUID text/display_field_key UTF-8 bytes/
-- field_assertion UUID text, is property_id|field_assertion_id|route_version|
-- base64(display_field_key UTF-8).  Fact IDs bind immutable assertions and
-- their immutable evidence/rights lineage transitively; JSON text is never
-- part of this digest contract.
create or replace function ops.tour_canonical_projection_digest(p_tenant text,p_projection_id uuid)
returns text language plpgsql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_projection ops.tour_public_projection%rowtype; v_fact_lines text; v_bytes text;
begin
  select * into v_projection from ops.tour_public_projection where id=p_projection_id and organization_tenant_id=p_tenant;
  if not found then raise exception 'canonical projection digest target is unavailable'; end if;
  select string_agg(
    f.property_id::text || '|' || f.field_assertion_id::text || '|' || f.route_version::text || '|' ||
    replace(encode(convert_to(f.display_field_key,'UTF8'),'base64'),E'\n',''),
    E'\n' order by f.property_id::text,convert_to(f.display_field_key,'UTF8'),f.field_assertion_id::text
  ) into v_fact_lines
    from ops.tour_public_projection_fact f
   where f.organization_tenant_id=p_tenant and f.projection_id=p_projection_id;
  v_bytes := array_to_string(array[
    'public-tour-projection-digest.v1',
    replace(encode(convert_to(p_tenant,'UTF8'),'base64'),E'\n',''),
    v_projection.tour_id::text,
    p_projection_id::text,
    v_projection.projection_version::text,
    v_projection.route_version::text,
    to_char(date_trunc('milliseconds',v_projection.as_of at time zone 'UTC'),'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
  ],E'\n') || case when v_fact_lines is null then '' else E'\n' || v_fact_lines end;
  return 'sha256:' || encode(public.digest(convert_to(v_bytes,'UTF8'),'sha256'),'hex');
end $$;

create or replace function ops.tour_projection_seal_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_projection ops.tour_public_projection%rowtype; v_digest text;
begin
  select * into v_projection from ops.tour_public_projection where id=new.projection_id and organization_tenant_id=new.organization_tenant_id for update;
  if not found then raise exception 'projection seal target is unavailable'; end if;
  if new.sealed_state <> 'approved' then raise exception 'projection seal cannot publish'; end if;
  if v_projection.status <> 'draft' or exists (select 1 from ops.tour_public_projection_seal_receipt s where s.organization_tenant_id=v_projection.organization_tenant_id and s.projection_id=v_projection.id) then raise exception 'projection is not an unsealed draft'; end if;
  if exists (
    select 1 from ops.tour_property_membership m
    cross join (values ('display.name'::text),('display.address'::text)) required(field_key)
    where m.organization_tenant_id=new.organization_tenant_id and m.tour_id=v_projection.tour_id and m.route_version=v_projection.route_version
      and not exists (select 1 from ops.tour_public_projection_fact f where f.organization_tenant_id=m.organization_tenant_id and f.projection_id=v_projection.id and f.property_id=m.property_id and f.display_field_key=required.field_key)
  ) then raise exception 'projection seal requires one complete selected-property fact set'; end if;
  v_digest:=ops.tour_canonical_projection_digest(new.organization_tenant_id,new.projection_id);
  if new.canonical_projection_digest is distinct from v_digest then raise exception 'projection canonical digest is database-computed'; end if;
  update ops.tour_public_projection set status='approved',projection_digest=v_digest
    where id=v_projection.id and organization_tenant_id=v_projection.organization_tenant_id;
  return new;
end $$;

create or replace function ops.tour_share_revocation_receipt_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_created_at timestamptz;
begin
  select created_at into v_created_at from ops.tour_share_grant
   where id=new.share_grant_id and organization_tenant_id=new.organization_tenant_id;
  if not found or new.revoked_at < v_created_at then raise exception 'share grant revocation receipt lineage is invalid'; end if;
  return new;
end $$;

-- This is the only application-executable authority mutation path.  Facts and
-- its seal receipt are inserted in one transaction; either the whole selected
-- property set and its database-computed digest land, or none of them do.
create or replace function ops.seal_tour_public_projection(
  p_tenant text,p_projection_id uuid,p_selected_facts jsonb,p_actor_id text,p_receipt_digest text
) returns text language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_projection ops.tour_public_projection%rowtype; v_digest text;
begin
  if jsonb_typeof(p_selected_facts) <> 'array' or jsonb_array_length(p_selected_facts)=0
     or exists (select 1 from jsonb_array_elements(p_selected_facts) item where jsonb_typeof(item)<>'object' or (select array_agg(k order by k) from jsonb_object_keys(item) k) is distinct from array['display_field_key','field_assertion_id','property_id'])
     or exists (select 1 from jsonb_array_elements(p_selected_facts) item where coalesce(item->>'property_id','') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' or coalesce(item->>'field_assertion_id','') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' or coalesce(item->>'display_field_key','')='')
  then raise exception 'projection seal selected facts are invalid'; end if;
  select * into v_projection from ops.tour_public_projection where id=p_projection_id and organization_tenant_id=p_tenant for update;
  if not found then raise exception 'projection seal target is unavailable'; end if;
  if v_projection.status <> 'draft' or exists (select 1 from ops.tour_public_projection_seal_receipt where organization_tenant_id=p_tenant and projection_id=p_projection_id) then raise exception 'projection is not an unsealed draft'; end if;
  if exists (select 1 from ops.tour_public_projection_fact where organization_tenant_id=p_tenant and projection_id=p_projection_id) then raise exception 'projection seal facts already exist'; end if;
  if exists (select 1 from jsonb_to_recordset(p_selected_facts) x(property_id uuid,field_assertion_id uuid,display_field_key text) group by property_id,display_field_key having count(*)<>1) then raise exception 'projection seal selected facts are duplicated'; end if;
  if exists (
    select 1 from ops.tour_property_membership m
    cross join (values ('display.name'::text),('display.address'::text)) required(field_key)
    where m.organization_tenant_id=p_tenant and m.tour_id=v_projection.tour_id and m.route_version=v_projection.route_version
      and not exists (select 1 from jsonb_to_recordset(p_selected_facts) x(property_id uuid,field_assertion_id uuid,display_field_key text) where x.property_id=m.property_id and x.display_field_key=required.field_key)
  ) then raise exception 'projection seal requires one complete selected-property fact set'; end if;
  insert into ops.tour_public_projection_fact (organization_tenant_id,projection_id,property_id,field_assertion_id,route_version,display_field_key)
  select p_tenant,p_projection_id,x.property_id,x.field_assertion_id,v_projection.route_version,x.display_field_key
    from jsonb_to_recordset(p_selected_facts) x(property_id uuid,field_assertion_id uuid,display_field_key text);
  v_digest:=ops.tour_canonical_projection_digest(p_tenant,p_projection_id);
  insert into ops.tour_public_projection_seal_receipt (organization_tenant_id,projection_id,sealed_at,sealed_state,actor_id,receipt_digest,canonical_projection_digest)
  values (p_tenant,p_projection_id,now(),'approved',p_actor_id,p_receipt_digest,v_digest);
  return v_digest;
end $$;

drop trigger if exists tour_publication_creation_guard on ops.tour_publication;
create trigger tour_publication_creation_guard before insert on ops.tour_publication
  for each row execute function ops.tour_publication_creation_guard();
drop trigger if exists tour_share_revocation_receipt_guard on ops.tour_share_grant_revocation_receipt;
create trigger tour_share_revocation_receipt_guard before insert on ops.tour_share_grant_revocation_receipt
  for each row execute function ops.tour_share_revocation_receipt_guard();
drop trigger if exists tour_public_projection_append_only on ops.tour_public_projection;
create trigger tour_public_projection_append_only before update or delete on ops.tour_public_projection
  for each row execute function ops.tour_public_projection_mutation_guard();


-- Narrow app seam.  Payload-to-row conversion is inside SECURITY DEFINER
-- functions; table DML remains unavailable to every application role.
create or replace function ops.append_tour_rights_receipt(p_payload jsonb)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v ops.tour_rights_receipt%rowtype; v_id uuid;
begin
  select * into v from jsonb_populate_record(null::ops.tour_rights_receipt,p_payload);
  if v.id is not null or v.organization_tenant_id is null or v.provider is null or v.policy_key is null
     or v.receipt_version is null or v.receipt_digest is null or v.terms_url is null
     or v.reviewed_at is null or v.reviewer is null or v.intended_use is null
     or v.allowed_field_classes is null or v.allowed_use_classes is null or v.effective_at is null
     or v.status is distinct from 'active' or v.revoked_at is not null then
    raise exception 'rights receipt append payload is invalid';
  end if;
  insert into ops.tour_rights_receipt (organization_tenant_id,provider,sku,policy_key,receipt_version,receipt_digest,terms_url,reviewed_at,reviewer,intended_use,allowed_field_classes,allowed_use_classes,effective_at,expires_at,revoked_at,supersedes_receipt_id,status)
  values (v.organization_tenant_id,v.provider,v.sku,v.policy_key,v.receipt_version,v.receipt_digest,v.terms_url,v.reviewed_at,v.reviewer,v.intended_use,v.allowed_field_classes,v.allowed_use_classes,v.effective_at,v.expires_at,v.revoked_at,v.supersedes_receipt_id,v.status)
  returning id into v_id;
  return v_id;
end $$;

create or replace function ops.revoke_tour_rights_receipt(
  p_tenant text,p_rights_receipt_id uuid,p_revoked_at timestamptz,p_actor_id text,p_receipt_digest text
) returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_prior ops.tour_rights_receipt%rowtype; v_id uuid;
begin
  select * into v_prior from ops.tour_rights_receipt
   where id=p_rights_receipt_id and organization_tenant_id=p_tenant for update;
  if not found or v_prior.status <> 'active' or v_prior.revoked_at is not null or p_revoked_at is null
     or p_actor_id is null or p_receipt_digest is null then
    raise exception 'rights receipt revocation target is invalid';
  end if;
  insert into ops.tour_rights_receipt (organization_tenant_id,provider,sku,policy_key,receipt_version,receipt_digest,terms_url,reviewed_at,reviewer,intended_use,allowed_field_classes,allowed_use_classes,effective_at,revoked_at,supersedes_receipt_id,status)
  values (p_tenant,v_prior.provider,v_prior.sku,v_prior.policy_key,v_prior.receipt_version+1,p_receipt_digest,v_prior.terms_url,p_revoked_at,p_actor_id,'revocation',v_prior.allowed_field_classes,v_prior.allowed_use_classes,p_revoked_at,p_revoked_at,v_prior.id,'revoked')
  returning id into v_id;
  return v_id;
end $$;

create or replace function ops.append_tour_source_evidence(p_payload jsonb)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v ops.tour_source_evidence%rowtype; v_id uuid;
begin
  select * into v from jsonb_populate_record(null::ops.tour_source_evidence,p_payload);
  if v.id is not null or v.organization_tenant_id is null or v.stable_locator is null
     or v.evidence_class is null or v.retrieved_at is null or v.retrieval_status is null
     or v.content_digest is null or v.rights_receipt_id is null or v.rights_provider is null
     or v.rights_policy_key is null or v.data_classification is null then
    raise exception 'source evidence append payload is invalid';
  end if;
  insert into ops.tour_source_evidence (organization_tenant_id,stable_locator,evidence_class,retrieved_at,retrieval_status,content_digest,rights_receipt_id,rights_provider,rights_policy_key,data_classification)
  values (v.organization_tenant_id,v.stable_locator,v.evidence_class,v.retrieved_at,v.retrieval_status,v.content_digest,v.rights_receipt_id,v.rights_provider,v.rights_policy_key,v.data_classification)
  returning id into v_id;
  return v_id;
end $$;

create or replace function ops.append_tour_field_assertion(p_payload jsonb)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v ops.tour_field_assertion%rowtype; v_id uuid;
begin
  select * into v from jsonb_populate_record(null::ops.tour_field_assertion,p_payload);
  if v.id is not null or v.organization_tenant_id is null or v.property_id is null
     or v.field_key is null or v.value is null or v.source_evidence_id is null or v.rights_receipt_id is null
     or v.observed_at is null or v.effective_from is null or v.confidence is null
     or v.data_classification is null or v.review_state is null then
    raise exception 'field assertion append payload is invalid';
  end if;
  insert into ops.tour_field_assertion (organization_tenant_id,property_id,field_key,value,source_evidence_id,rights_receipt_id,observed_at,effective_from,effective_to,confidence,data_classification,review_state)
  values (v.organization_tenant_id,v.property_id,v.field_key,v.value,v.source_evidence_id,v.rights_receipt_id,v.observed_at,v.effective_from,v.effective_to,v.confidence,v.data_classification,v.review_state)
  returning id into v_id;
  return v_id;
end $$;

create or replace function ops.create_tour_public_projection_draft(
  p_tenant text,p_tour_id uuid,p_projection_version integer,p_route_version integer,p_as_of timestamptz
) returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_id uuid; v_digest text;
begin
  if p_tenant is null or p_tour_id is null or p_projection_version is null
     or p_route_version is null or p_as_of is null then
    raise exception 'projection draft payload is invalid';
  end if;
  v_digest := 'sha256:' || encode(public.digest(jsonb_build_object(
    'organization_tenant_id',p_tenant,'tour_id',p_tour_id,'projection_version',p_projection_version,
    'route_version',p_route_version,'as_of',p_as_of,'facts','[]'::jsonb,'kind','draft')::text,'sha256'),'hex');
  insert into ops.tour_public_projection (organization_tenant_id,tour_id,projection_version,route_version,as_of,facts_only,projection_digest,status)
  values (p_tenant,p_tour_id,p_projection_version,p_route_version,p_as_of,true,v_digest,'draft')
  returning id into v_id;
  return v_id;
end $$;

-- Safe client read: values are joined only from the reviewed public assertion
-- selected into the sealed fact.  It deliberately exposes no contacts, locator,
-- provider terms, or non-public assertion fields.
create or replace function ops.read_tour_public_projection(p_tenant text,p_projection_id uuid)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  with clock as materialized (select statement_timestamp() as evaluated_at)
  select jsonb_build_object(
    'projection_id',p.id,'tour_id',p.tour_id,'projection_version',p.projection_version,
    'route_version',p.route_version,'as_of',p.as_of,'projection_digest',p.projection_digest,
    'facts',coalesce((
      select jsonb_agg(jsonb_build_object(
        'property_id',f.property_id,'field_assertion_id',f.field_assertion_id,
        'display_field_key',f.display_field_key,'value',a.value,
        'source_evidence_id',a.source_evidence_id,'rights_receipt_id',a.rights_receipt_id,
        'observed_at',a.observed_at,'effective_from',a.effective_from,'effective_to',a.effective_to
      ) order by f.property_id::text,f.display_field_key,f.field_assertion_id::text)
      from ops.tour_public_projection_fact f
      join ops.tour_field_assertion a on a.id=f.field_assertion_id and a.organization_tenant_id=f.organization_tenant_id
      join ops.tour_source_evidence e on e.id=a.source_evidence_id and e.organization_tenant_id=a.organization_tenant_id
      join ops.tour_rights_receipt r on r.id=a.rights_receipt_id and r.organization_tenant_id=a.organization_tenant_id
      where f.organization_tenant_id=p.organization_tenant_id and f.projection_id=p.id
        and a.review_state='reviewed' and a.data_classification='public'
        and a.rights_receipt_id=e.rights_receipt_id
        and r.provider=e.rights_provider and r.policy_key=e.rights_policy_key
    ),'[]'::jsonb))
  from ops.tour_public_projection p
  cross join clock
  where p.organization_tenant_id=p_tenant and p.id=p_projection_id and p.status='approved'
    and exists (select 1 from ops.tour_public_projection_seal_receipt s where s.organization_tenant_id=p.organization_tenant_id and s.projection_id=p.id and s.canonical_projection_digest=p.projection_digest)
    and not exists (
      select 1 from ops.tour_public_projection_fact f
      join ops.tour_field_assertion a on a.id=f.field_assertion_id and a.organization_tenant_id=f.organization_tenant_id
      join ops.tour_source_evidence e on e.id=a.source_evidence_id and e.organization_tenant_id=a.organization_tenant_id
      join ops.tour_rights_receipt r on r.id=a.rights_receipt_id and r.organization_tenant_id=a.organization_tenant_id
      where f.organization_tenant_id=p.organization_tenant_id and f.projection_id=p.id
        and (a.review_state <> 'reviewed' or a.data_classification <> 'public'
          or a.rights_receipt_id <> e.rights_receipt_id
          or r.provider <> e.rights_provider or r.policy_key <> e.rights_policy_key
          or e.retrieval_status <> 'read' or e.retrieved_at > p.as_of
          or a.confidence not in ('low','medium','high') or a.observed_at > p.as_of
          or r.status <> 'active' or r.effective_at > clock.evaluated_at
          or (r.expires_at is not null and r.expires_at <= clock.evaluated_at)
          or (r.revoked_at is not null and r.revoked_at <= clock.evaluated_at)
          or not (r.allowed_use_classes ? 'client_public_display')
          or not (r.allowed_field_classes ? a.field_key or r.allowed_field_classes ? '*')
          or exists (
            select 1 from ops.tour_rights_receipt newer
            where newer.organization_tenant_id=r.organization_tenant_id
              and newer.provider=r.provider and newer.policy_key=r.policy_key
              and newer.receipt_version > r.receipt_version
              and newer.effective_at <= clock.evaluated_at
          )
          or exists (
            select 1 from ops.tour_fact_conflict conflict
            where conflict.organization_tenant_id=a.organization_tenant_id
              and conflict.property_id=a.property_id and conflict.field_key=a.field_key
              and conflict.state <> 'superseded' and conflict.opened_at <= clock.evaluated_at
              and not exists (
                select 1 from ops.tour_conflict_resolution_receipt resolution
                where resolution.organization_tenant_id=conflict.organization_tenant_id
                  and resolution.conflict_id=conflict.id
                  and resolution.selected_field_assertion_id=a.id
                  and resolution.resolved_at <= clock.evaluated_at
              )
          ))
    );
$$;

revoke all on table ops.tour_rights_receipt,ops.tour_source_evidence,ops.tour_field_assertion,
  ops.tour_property,ops.tour_fact_conflict,ops.tour_fact_conflict_participant,
  ops.tour_conflict_resolution_receipt,ops.tour,ops.tour_property_membership,
  ops.tour_public_projection,ops.tour_public_projection_fact,
  ops.tour_public_projection_seal_receipt,ops.tour_cheat_sheet_revision,
  ops.tour_share_grant,ops.tour_share_grant_revocation_receipt,ops.tour_qc_finding,
  ops.tour_publication,ops.tour_audit_event from public,carr_reader,carr_writer,carr_jobs,carr_authority;

grant select on ops.tour_public_projection,ops.tour_public_projection_fact,
  ops.tour_public_projection_seal_receipt,ops.tour_publication,ops.tour_share_grant
  to carr_reader;
grant select on ops.tour_rights_receipt,ops.tour_source_evidence,ops.tour_field_assertion,
  ops.tour_property,ops.tour_fact_conflict,ops.tour_fact_conflict_participant,
  ops.tour_conflict_resolution_receipt,ops.tour,ops.tour_property_membership,
  ops.tour_public_projection,ops.tour_public_projection_fact,
  ops.tour_public_projection_seal_receipt,ops.tour_cheat_sheet_revision,
  ops.tour_share_grant,ops.tour_share_grant_revocation_receipt,ops.tour_qc_finding,
  ops.tour_publication,ops.tour_audit_event to carr_writer,carr_authority;
grant select on ops.tour_public_projection,ops.tour_public_projection_fact,
  ops.tour_public_projection_seal_receipt to carr_jobs;

revoke all on function ops.tour_rights_provider_policy_lock(text,text,text),
  ops.tour_rights_lineage_guard(),ops.tour_source_rights_guard(),ops.tour_assertion_rights_guard(),
  ops.tour_public_value_safe(text,jsonb),ops.tour_projection_creation_guard(),
  ops.tour_publication_creation_guard(),ops.tour_projection_fact_guard(),
  ops.tour_public_projection_mutation_guard(),ops.tour_canonical_projection_digest(text,uuid),ops.tour_projection_seal_guard(),
  ops.tour_share_revocation_receipt_guard(),ops.seal_tour_public_projection(text,uuid,jsonb,text,text)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.append_tour_rights_receipt(jsonb),
  ops.revoke_tour_rights_receipt(text,uuid,timestamp with time zone,text,text),
  ops.append_tour_source_evidence(jsonb),ops.append_tour_field_assertion(jsonb),
  ops.create_tour_public_projection_draft(text,uuid,integer,integer,timestamp with time zone),
  ops.read_tour_public_projection(text,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.append_tour_rights_receipt(jsonb),
  ops.revoke_tour_rights_receipt(text,uuid,timestamp with time zone,text,text) to carr_authority;
grant execute on function ops.append_tour_source_evidence(jsonb),
  ops.create_tour_public_projection_draft(text,uuid,integer,integer,timestamp with time zone) to carr_writer;
grant execute on function ops.append_tour_field_assertion(jsonb) to carr_authority;
grant execute on function ops.read_tour_public_projection(text,uuid)
  to carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.seal_tour_public_projection(text,uuid,jsonb,text,text) to carr_authority;

commit;
-- Rollback is forward-only: revoke the seal verb and quarantine derived clients; never rewrite evidence or receipts.
