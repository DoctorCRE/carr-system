-- rollback: remove session consumers before dropping these private functions and table.
-- lock-review: serializes only one portal row; no existing table rewrite.

create function ops.client_portal_projection(p_slug text) returns jsonb
language plpgsql stable set search_path=pg_catalog,pg_temp as $$
declare portal ops.client_portal%rowtype; result jsonb;
begin
  select p.* into portal from ops.client_portal p where p.slug=p_slug and p.disabled_at is null;
  if portal.slug is null then return null; end if;
  if (select count(*) from ops.client_portal_material m where m.portal_slug=portal.slug
      and m.shared_at<=now() and m.withdrawn_at is null)>100 then
    return null;
  end if;
  select jsonb_build_object(
    'slug',portal.slug,
    'roadmap',jsonb_build_object('current_phase',d.phase,'phases',
      (select coalesce(jsonb_agg(jsonb_build_object('slug',ph.slug,'label',ph.label) order by ph.sort,ph.slug),'[]'::jsonb) from public.deal_phase ph)),
    'materials',(select coalesce(jsonb_agg(jsonb_build_object('kind',m.kind,'title',m.title,'content',m.content) order by m.shared_at,m.id),'[]'::jsonb)
      from ops.client_portal_material m where m.portal_slug=portal.slug and m.shared_at<=now() and m.withdrawn_at is null)
  ) into result
  from public.deal d join public.client cl on cl.id=d.client_id join public.party p on p.id=cl.party_id
  where d.id=portal.deal_id and cl.id=portal.client_id and cl.merged_into is null
    and p.merged_into is null and p.deleted_at is null;
  return result;
end $$;
revoke all on function ops.client_portal_projection(text) from public,carr_reader,carr_writer,carr_authority,dot_reader;

create or replace function ops.read_client_portal(p_slug text,p_password text) returns jsonb
language plpgsql volatile security definer set search_path=pg_catalog,pg_temp as $$
declare
  portal ops.client_portal%rowtype;
  observed_hash text;
  supplied_hash text;
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
  return ops.client_portal_projection(p_slug);
end $$;

create table ops.client_portal_session (
  digest text primary key check (digest ~ '^[0-9a-f]{64}$'),
  portal_slug text not null references ops.client_portal(slug),
  created_at timestamptz not null,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  check (expires_at = created_at + interval '168 hours')
);
create index client_portal_session_slug_expiry on ops.client_portal_session(portal_slug,expires_at);
revoke all on ops.client_portal_session from public,carr_reader,carr_writer,carr_authority,dot_reader;

-- Retain digest ownership after expiry cleanup so a retry can never reissue it.
create table ops.client_portal_session_digest (
  digest text primary key check (digest ~ '^[0-9a-f]{64}$'),
  portal_slug text not null references ops.client_portal(slug)
);
revoke all on ops.client_portal_session_digest from public,carr_reader,carr_writer,carr_authority,dot_reader;

create function ops.exchange_client_portal_session(p_slug text,p_password text,p_digest text) returns jsonb
language plpgsql volatile security definer set search_path=pg_catalog,pg_temp as $$
declare
  projection jsonb;
  portal ops.client_portal%rowtype;
  session ops.client_portal_session%rowtype;
  issued_at timestamptz;
  denied constant jsonb := '{"status":"denied","error":"portal_access_denied"}';
begin
  if p_slug is null or p_slug !~ '^[a-z0-9][a-z0-9-]{0,62}$'
     or p_digest is null or p_digest !~ '^[0-9a-f]{64}$'
     or p_password is null or length(p_password) not between 1 and 1024 then
    return denied;
  end if;
  -- Reuse the existing verifier and its atomic shared lockout accounting.
  projection := ops.read_client_portal(p_slug,p_password);
  select p.* into portal from ops.client_portal p where p.slug=p_slug for update;
  issued_at := clock_timestamp();
  if portal.failed_attempts >= 5 and portal.attempt_window_started_at > issued_at - interval '15 minutes' then
    return jsonb_build_object('status','locked','error','portal_locked',
      'retry_at',portal.attempt_window_started_at + interval '15 minutes');
  end if;
  if projection is null then return denied; end if;
  select s.* into session from ops.client_portal_session s where s.digest=p_digest;
  if found then
    if session.portal_slug<>p_slug or session.revoked_at is not null or session.expires_at<=issued_at then
      return denied;
    end if;
    return jsonb_build_object('status','success','expires_at',session.expires_at);
  end if;
  delete from ops.client_portal_session where portal_slug=p_slug and expires_at<=issued_at;
  if (select count(*) from ops.client_portal_session where portal_slug=p_slug)>=10 then return denied; end if;
  insert into ops.client_portal_session_digest(digest,portal_slug) values(p_digest,p_slug)
    on conflict (digest) do nothing;
  if not found then return denied; end if;
  insert into ops.client_portal_session(digest,portal_slug,created_at,expires_at)
    values(p_digest,p_slug,issued_at,issued_at + interval '168 hours') on conflict (digest) do nothing;
  if not found then return denied; end if;
  return jsonb_build_object('status','success','expires_at',issued_at + interval '168 hours');
end $$;

create function ops.read_client_portal_session(p_slug text,p_digest text) returns jsonb
language plpgsql volatile security definer set search_path=pg_catalog,pg_temp as $$
declare projection jsonb;
begin
  if p_slug is not null and p_slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'
     and p_digest is not null and p_digest ~ '^[0-9a-f]{64}$' then
    perform 1 from ops.client_portal where slug=p_slug and disabled_at is null for share;
    if found and exists(select 1 from ops.client_portal_session where digest=p_digest
      and portal_slug=p_slug and revoked_at is null and expires_at>clock_timestamp()) then
      projection := ops.client_portal_projection(p_slug);
      if projection is not null then
        return jsonb_build_object('status','success','schema_version','client-portal.v1','portal',projection);
      end if;
    end if;
  end if;
  return '{"status":"denied","error":"portal_access_denied"}'::jsonb;
end $$;

create function ops.revoke_client_portal_session(p_slug text,p_digest text) returns jsonb
language plpgsql volatile security definer set search_path=pg_catalog,pg_temp as $$
begin
  if p_slug is not null and p_slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'
     and p_digest is not null and p_digest ~ '^[0-9a-f]{64}$' then
    perform 1 from ops.client_portal where slug=p_slug for update;
    update ops.client_portal_session set revoked_at=coalesce(revoked_at,clock_timestamp())
      where digest=p_digest and portal_slug=p_slug;
    if found then return '{"status":"success"}'::jsonb; end if;
  end if;
  return '{"status":"denied","error":"portal_access_denied"}'::jsonb;
end $$;

revoke all on function ops.exchange_client_portal_session(text,text,text),
  ops.read_client_portal_session(text,text),ops.revoke_client_portal_session(text,text)
  from public,carr_reader,carr_writer,carr_authority,dot_reader;
grant execute on function ops.exchange_client_portal_session(text,text,text),
  ops.read_client_portal_session(text,text),ops.revoke_client_portal_session(text,text) to carr_writer;

do $contract$
declare contract jsonb; entry jsonb; signature text;
begin
  contract := ops.scac_current_grant_contract();
  foreach signature in array array[
    'ops.exchange_client_portal_session(p_slug text, p_password text, p_digest text)',
    'ops.read_client_portal_session(p_slug text, p_digest text)',
    'ops.revoke_client_portal_session(p_slug text, p_digest text)'] loop
    entry := jsonb_build_object('config',jsonb_build_array('search_path=pg_catalog, pg_temp'),
      'grantee','carr_writer','parallel','u','grantable',false,'privilege','execute',
      'signature',signature,'volatility','v','ingress_key','db-function-acl:'||signature||':carr_writer:execute',
      'ingress_kind','db_function_acl','function_kind','f','security_definer',true);
    contract := jsonb_set(contract,'{secdef_execute}',(contract->'secdef_execute')||jsonb_build_array(entry));
  end loop;
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L',
    'select '||quote_literal(contract::text)||'::jsonb');
end $contract$;
