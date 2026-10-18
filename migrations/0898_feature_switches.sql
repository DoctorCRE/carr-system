-- App feature configuration extends the existing versioned record/audit envelope.
-- rollback: forward-only — retain switch state and audit history; retire or restore switches through their audited verbs.
create table public.feature_switch (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  name text not null check (name ~ '^[a-z][a-z0-9-]{0,62}$'),
  description text not null check (length(btrim(description)) between 1 and 1000),
  default_enabled boolean not null,
  enabled boolean,
  audience text not null check (audience in ('joe','team','everyone')),
  owner text not null check (owner in ('joe','dell','claude')),
  created_at timestamptz not null default now(),
  expected_removal_on date not null,
  retired_at timestamptz,
  retirement_loop_id uuid,
  version integer not null default 1 check (version > 0),
  unique (organization_tenant_id,name)
);
grant select on public.feature_switch to carr_reader,carr_writer;
grant insert,update on public.feature_switch to carr_writer;
create trigger scac_reference_monitor_guard_row before insert or update or delete
on public.feature_switch for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on public.feature_switch for each statement execute function ops.scac_reference_monitor_guard();

-- The first consumer ships hidden; activation is a later audited record act.
insert into public.feature_switch
  (organization_tenant_id,name,description,default_enabled,audience,owner,expected_removal_on)
values ('carr-internal','doc-suggestion-actions',
        'Act on or correct a suggestion from Doc',false,'joe','claude','2026-11-05');
