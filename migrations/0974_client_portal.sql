-- rollback: remove the portal consumer before dropping these empty-by-default records and read function.
-- lock-review: adds portal-only tables and a reader function; existing business records are untouched.

create table ops.client_portal (
  slug text primary key check (slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
  client_id uuid not null references public.client(id),
  deal_id uuid not null references public.deal(id),
  password_hash text not null check (password_hash ~ '^\$2[aby]\$12\$[./A-Za-z0-9]{53}$'),
  disabled_at timestamptz,
  created_at timestamptz not null default now()
);

create table ops.client_portal_material (
  id bigint generated always as identity primary key,
  portal_slug text not null references ops.client_portal(slug),
  kind text not null check (kind in ('document','tour','report')),
  title text not null check (length(btrim(title)) between 1 and 200),
  content text not null check (length(btrim(content)) between 1 and 10000),
  shared_at timestamptz,
  withdrawn_at timestamptz,
  created_at timestamptz not null default now()
);
create index client_portal_material_shared on ops.client_portal_material(portal_slug,shared_at,id)
  where shared_at is not null and withdrawn_at is null;

revoke all on ops.client_portal,ops.client_portal_material from public,carr_reader,carr_writer,carr_authority;
revoke all on sequence ops.client_portal_material_id_seq from public,carr_reader,carr_writer,carr_authority;

-- Verifiers use bcrypt(SHA-256(password)), so passwords differing beyond bcrypt's 72-byte limit remain distinct.
create function ops.read_client_portal(p_slug text,p_password text) returns jsonb
language plpgsql stable security definer set search_path=pg_catalog as $$
declare
  portal ops.client_portal%rowtype;
  supplied_hash text;
  result jsonb;
begin
  if p_slug is null or p_slug !~ '^[a-z0-9][a-z0-9-]{0,62}$'
     or p_password is null or length(p_password) not between 1 and 1024 then
    return null;
  end if;
  select p.* into portal from ops.client_portal p where p.slug=p_slug;
  -- A non-secret dummy verifier keeps an unknown slug on the same bcrypt work path.
  supplied_hash := public.crypt(encode(public.digest(p_password,'sha256'),'hex'),
    coalesce(portal.password_hash,'$2a$12$......................0000000000000000000000000000000'));
  if portal.slug is null or portal.password_hash is distinct from supplied_hash or portal.disabled_at is not null then
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
revoke all on function ops.read_client_portal(text,text) from public;
grant usage on schema ops to carr_reader;
grant execute on function ops.read_client_portal(text,text) to carr_reader;
