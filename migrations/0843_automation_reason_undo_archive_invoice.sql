-- W3c: explainable moves, guarded undo, permanent-exit proposals and local invoices.
insert into lead_stage(slug,label,sort) values ('archived','Archived',110);
alter table lead_stage_move add column reason text;
update lead_stage_move m set reason=(case a.kind when 'email_in' then 'Reply received'
  when 'email_out' then 'Approved first contact sent' when 'tour' then 'Tour held'
  when 'call' then 'Call held' else 'Meeting held' end)||' '||to_char(a.occurred_at at time zone 'UTC','YYYY-MM-DD')
from activity a where a.id=m.activity_id;
alter table lead_stage_move alter column reason set not null;
alter table lead_stage_move add constraint lead_move_reason_nonempty check(length(trim(reason))>0);
alter table lead_stage_move add column undone_at timestamptz;
alter table lead_stage_move add column undone_by uuid references actor(id);
alter table lead_stage_move drop constraint lead_stage_move_status_check;
alter table lead_stage_move add constraint lead_stage_move_status_check check(status in ('proposed','applied','undone'));
alter table lead_stage_move drop constraint lead_stage_move_check;
alter table lead_stage_move add constraint lead_stage_move_check check (
  (from_stage='new' and to_stage='qualified') or
  (from_stage='qualified' and to_stage='outreach_active') or
  (from_stage='outreach_active' and to_stage='engaged') or
  (from_stage='engaged' and to_stage in ('nurture_drip','opportunity')) or
  (from_stage in ('new','qualified','outreach_active','engaged','nurture_drip','opportunity') and to_stage='archived'));
alter table lead_stage_move add constraint archive_partner_required check (
  to_stage<>'archived' or status='proposed' or (approved_at is not null and approved_by is not null));
alter table lead_stage_move add constraint lead_move_undo_actor check (
  (status='undone')=(undone_at is not null) and (undone_at is null)=(undone_by is null));

create table deal_invoice_email (
  id uuid primary key default gen_random_uuid(),
  evidence_ref text not null unique check(length(trim(evidence_ref))>0),
  from_address text not null,
  deal_name text not null check(length(trim(deal_name))>0),
  client_name text,
  property_address text,
  occurred_at timestamptz not null,
  email_date date not null,
  created_by uuid not null references actor(id),
  status text not null default 'captured' check(status in ('captured','applied','undone')),
  deal_id uuid references deal(id),
  prior_phase text references deal_phase(slug),
  prior_invoiced_on date,
  phase_event_id uuid references event(id),
  invoice_event_id uuid references event(id),
  applied_at timestamptz,
  applied_by uuid references actor(id),
  reason text,
  undone_at timestamptz,
  undone_by uuid references actor(id),
  check(status='captured' or (deal_id is not null and prior_phase is not null and phase_event_id is not null
    and invoice_event_id is not null and applied_at is not null and applied_by is not null and reason is not null and length(trim(reason))>0)),
  check((status='undone')=(undone_at is not null) and (undone_at is null)=(undone_by is null))
);
grant select on deal_invoice_email to carr_reader,carr_writer;
grant insert,update on deal_invoice_email to carr_writer;
create trigger scac_reference_monitor_guard_row before insert or update or delete
on public.deal_invoice_email for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on public.deal_invoice_email for each statement execute function ops.scac_reference_monitor_guard();
