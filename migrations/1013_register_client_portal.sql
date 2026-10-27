-- rollback: forward-only; drop ops.register_client_portal and public.v_deal_client_portal, but keep registered portal rows — they are the client's credentials of record.
-- expand-contract: expand — one new function and one new view; no existing object changes.
-- lock-review: partial index scans the small client portal table during migration.

create unique index client_portal_one_active_per_deal on ops.client_portal(deal_id) where disabled_at is null;

-- Registers the portal for one deal. Only the bcrypt(SHA-256(password)) verifier
-- that ops.read_client_portal checks is stored; the password itself never is.
create function ops.register_client_portal(p_deal_id uuid, p_slug text, p_password text) returns text
language plpgsql volatile security definer set search_path=pg_catalog,pg_temp as $$
declare
  owner_client uuid;
  existing ops.client_portal%rowtype;
begin
  if p_slug is null or p_slug !~ '^[a-z0-9][a-z0-9-]{0,62}$' then
    raise exception 'portal_slug_invalid' using errcode='22023';
  end if;
  if p_password is null or length(p_password) not between 1 and 1024 then
    raise exception 'portal_password_invalid' using errcode='22023';
  end if;
  select d.client_id into owner_client from public.deal d
    join public.client cl on cl.id=d.client_id and cl.merged_into is null
   where d.id=p_deal_id;
  if owner_client is null then
    raise exception 'portal_deal_not_found' using errcode='22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('client-portal-deal:'||p_deal_id::text,0));
  perform pg_advisory_xact_lock(hashtextextended('client-portal:'||p_slug,0));
  select p.* into existing from ops.client_portal p where p.slug=p_slug;
  if found then
    if existing.client_id=owner_client and existing.deal_id=p_deal_id and existing.disabled_at is null then
      if existing.password_hash=public.crypt(encode(public.digest(p_password,'sha256'),'hex'),existing.password_hash) then
        return 'existing';
      end if;
      raise exception 'portal_password_mismatch' using errcode='23505';
    end if;
    raise exception 'portal_slug_taken' using errcode='23505';
  end if;
  if exists (select 1 from ops.client_portal p where p.deal_id=p_deal_id and p.disabled_at is null) then
    raise exception 'portal_deal_already_registered' using errcode='23505';
  end if;
  insert into ops.client_portal(slug,client_id,deal_id,password_hash)
  values (p_slug,owner_client,p_deal_id,
    public.crypt(encode(public.digest(p_password,'sha256'),'hex'),public.gen_salt('bf',12)));
  return 'created';
end $$;
revoke all on function ops.register_client_portal(uuid,text,text) from public;
grant usage on schema ops to carr_writer;
grant execute on function ops.register_client_portal(uuid,text,text) to carr_writer;

-- Deal Room readers have no access to ops.client_portal; they read the derived link here.
create view public.v_deal_client_portal as
select deal_id, 'https://share.doctorcre.com/' || slug as client_portal_url
  from ops.client_portal
 where disabled_at is null;
revoke all on public.v_deal_client_portal from dot_reader;
grant select on public.v_deal_client_portal to carr_reader, carr_writer;

do $contract$
declare contract jsonb := ops.scac_current_grant_contract();
begin
  contract := jsonb_set(contract, '{secdef_execute}', (contract->'secdef_execute') || jsonb_build_array(jsonb_build_object(
    'config', jsonb_build_array('search_path=pg_catalog, pg_temp'), 'grantee', 'carr_writer', 'parallel', 'u',
    'grantable', false, 'privilege', 'execute',
    'signature', 'ops.register_client_portal(p_deal_id uuid, p_slug text, p_password text)', 'volatility', 'v',
    'ingress_key', 'db-function-acl:ops.register_client_portal(p_deal_id uuid, p_slug text, p_password text):carr_writer:execute',
    'ingress_kind', 'db_function_acl', 'function_kind', 'f', 'security_definer', true)));
  execute format(
    'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(contract::text) || '::jsonb');
end $contract$;
