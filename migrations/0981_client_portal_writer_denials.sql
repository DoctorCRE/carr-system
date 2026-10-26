-- rollback: forward-only; retain committed password limits and uniform bcrypt work.
-- lock-review: adds two fixed-size counters; locks only the requested slug after bcrypt.

alter table ops.client_portal
  add column failed_attempts integer not null default 0 check (failed_attempts between 0 and 5),
  add column attempt_window_started_at timestamptz;

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

revoke execute on function ops.read_client_portal(text,text) from carr_reader;
grant usage on schema ops to carr_writer;
grant execute on function ops.read_client_portal(text,text) to carr_writer;

do $contract$
declare contract jsonb;
begin
  contract := ops.scac_current_grant_contract();
  contract := jsonb_set(contract, '{secdef_execute}', (
    select jsonb_agg(case when entry->>'signature'='ops.read_client_portal(p_slug text, p_password text)'
      then entry || jsonb_build_object('volatility','v','grantee','carr_writer','ingress_key',
        'db-function-acl:ops.read_client_portal(p_slug text, p_password text):carr_writer:execute') else entry end order by ordinal)
    from jsonb_array_elements(contract->'secdef_execute') with ordinality as entries(entry,ordinal)
  ));
  execute format(
    'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(contract::text) || '::jsonb'
  );
end $contract$;
