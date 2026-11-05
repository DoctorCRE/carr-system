-- rollback: forward-only; retain captured correspondence and party lineage.
-- expand-contract: expand; nullable party identity leaves existing role activities unchanged.
-- lock-review: nullable column and unvalidated constraints use brief catalog locks; partial index scans activity.
alter table public.activity add column party_id uuid;
alter table public.activity add constraint activity_party_fk foreign key (party_id) references public.party(id) not valid;
alter table public.activity add constraint activity_party_exclusive check
  (party_id is null or num_nonnulls(deal_id,client_id,lead_id,vendor_id)=0) not valid;
create index activity_party_timeline_idx on public.activity(party_id,occurred_at desc,id desc) where party_id is not null;

create or replace view public.v_subject_timeline as
select 'activity'::text as entry_kind,a.id,a.occurred_at,a.recorded_at,act.slug as actor,
  a.kind as verb,a.summary,a.detail,a.owed,
  coalesce(a.deal_id,a.client_id,a.lead_id,a.vendor_id,a.party_id) as subject_id,
  case when a.deal_id is not null then 'deal' when a.client_id is not null then 'client'
    when a.lead_id is not null then 'lead' when a.vendor_id is not null then 'vendor'
    when a.party_id is not null then 'party' else 'vendor' end as subject_type
from public.activity a join public.actor act on act.id=a.actor_id
union all
select 'event'::text,e.id,e.occurred_at,e.recorded_at,act.slug,e.verb,
  coalesce(nullif(btrim(e.new_value->>'summary'),''),e.field,e.verb),e.human_quote,
  null::text,e.subject_id,e.subject_type
from public.event e join public.actor act on act.id=e.actor_id;
