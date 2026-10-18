-- W3b: internal proposals and approval-only first-contact drafts. No send path.
create table lead_stage_move (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references lead(id),
  from_stage text not null references lead_stage(slug),
  to_stage text not null references lead_stage(slug),
  activity_id uuid not null references activity(id),
  evidence_ref text not null check (length(evidence_ref)>0),
  strength text not null check (strength in ('strong','weak')),
  status text not null check (status in ('proposed','applied')),
  created_at timestamptz not null default now(),
  created_by uuid not null references actor(id),
  approved_at timestamptz,
  approved_by uuid references actor(id),
  unique(lead_id,from_stage,to_stage,activity_id),
  check ((from_stage='new' and to_stage='qualified') or
         (from_stage='qualified' and to_stage='outreach_active') or
         (from_stage='outreach_active' and to_stage='engaged') or
         (from_stage='engaged' and to_stage in ('nurture_drip','opportunity'))),
  check (status='proposed' or strength='strong' or (approved_at is not null and approved_by is not null))
);
create table lead_contact_draft (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null unique references lead(id),
  party_id uuid not null references party(id),
  owner_id uuid references actor(id),
  subject text not null,
  body text not null,
  scheduled_for timestamptz not null,
  time_zone text not null,
  requires_human_send boolean not null default true check (requires_human_send),
  dispatchable boolean not null default false check (not dispatchable),
  created_at timestamptz not null default now(),
  created_by uuid not null references actor(id),
  approved_at timestamptz,
  approved_by uuid references actor(id),
  check ((approved_at is null)=(approved_by is null))
);
grant select on lead_stage_move,lead_contact_draft to carr_reader,carr_writer;
grant insert,update on lead_stage_move,lead_contact_draft to carr_writer;

-- Keep both new writable relations under the existing shadow reference monitor.
create trigger scac_reference_monitor_guard_row before insert or update or delete
on public.lead_stage_move for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on public.lead_stage_move for each statement execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_row before insert or update or delete
on public.lead_contact_draft for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on public.lead_contact_draft for each statement execute function ops.scac_reference_monitor_guard();
