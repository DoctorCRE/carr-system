-- Synthetic transaction-scoped W4 projection and grant proof.
\set ON_ERROR_STOP on
begin;
do $$
declare a uuid; p uuid; c uuid; d uuid; e uuid; row record;
begin
  select id into a from actor where slug='joe';
  insert into party(kind,name,created_by,updated_by) values('org','W4 Demo Practice',a,a) returning id into p;
  insert into client(party_id,created_by,updated_by) values(p,a,a) returning id into c;
  insert into deal(client_id,name,deal_type,phase,outcome,created_by,updated_by)
    values(c,'W4 Demo Assignment','renewal','closed','won',a,a) returning id into d;
  if not exists(select 1 from v_deal_room_board where id=d) then raise exception 'Uninvoiced closed deal disappeared'; end if;
  update deal set outcome='paused',operating_state='parked',parking_reason='other',parking_note='Demo unverified import',parked_at=now(),parked_by=a where id=d;
  if not exists(select 1 from v_deal_room_board where id=d and operating_state='parked' and parking_note='Demo unverified import') then raise exception 'Parking fields lost'; end if;
  update deal set operating_state='active',parking_reason=null,parking_note=null,parked_at=null,parked_by=null where id=d;
  if not exists(select 1 from v_deal_room_board where id=d and operating_state='active') then raise exception 'Revived paused assignment disappeared'; end if;
  update deal set outcome='lost' where id=d;
  if not exists(select 1 from v_deal_room_board where id=d) then raise exception 'Uninvoiced closed outcome hidden'; end if;
  insert into event(actor_id,subject_type,subject_id,verb,field,old_value,new_value,cause,agent_rationale,occurred_at,recorded_at)
    values(a,'deal',d,'patch-deal-field','phase','{"phase":"negotiation"}','{"phase":"legal"}','ingest_email','Draft prepared','2026-10-03','2026-10-04') returning id into e;
  select * into row from v_deal_room_phase_change where deal_id=d;
  if not found or row.event_id is distinct from e or row.automatic is distinct from true
     or row.prior_phase is distinct from 'negotiation' or row.phase is distinct from 'legal'
     or row.reason is distinct from 'Draft prepared'
     or row.evidence_date::timestamptz is distinct from '2026-10-03'::timestamptz
     or row.recorded_at::timestamptz is distinct from '2026-10-04'::timestamptz
  then raise exception 'Phase identity, evidence date or reason lost'; end if;
  insert into event(actor_id,subject_type,subject_id,verb,field,old_value,new_value,cause,occurred_at,recorded_at,via,client_id)
    values(a,'deal',d,'patch-deal-field','phase','{"phase":"legal"}','{"phase":"closing"}','automation_job','2026-10-04','2026-10-05','dealroom-cookie','dealroom-pwa') returning id into e;
  select * into row from v_deal_room_phase_change where deal_id=d;
  if not found or row.event_id is distinct from e or row.automatic is distinct from false
     or row.prior_phase is distinct from 'legal' or row.phase is distinct from 'closing'
     or row.reason is distinct from 'phase changed'
     or row.evidence_date::timestamptz is distinct from '2026-10-04'::timestamptz
     or row.recorded_at::timestamptz is distinct from '2026-10-05'::timestamptz
  then raise exception 'Manual correction retained automatic badge'; end if;
  insert into event(actor_id,subject_type,subject_id,verb,field,old_value,new_value,cause,occurred_at,recorded_at,via,client_id)
    values(a,'deal',d,'resolve-conflict','phase','{"phase":"closing"}','{"phase":"legal"}','automation_job','2026-10-05','2026-10-06','dealroom-cookie','dealroom-pwa') returning id into e;
  select * into row from v_deal_room_phase_change where deal_id=d;
  if not found or row.event_id is distinct from e or row.automatic is distinct from false
     or row.prior_phase is distinct from 'closing' or row.phase is distinct from 'legal'
  then raise exception 'Human conflict resolution retained automatic badge'; end if;
  insert into event(actor_id,subject_type,subject_id,verb,field,old_value,new_value,cause,occurred_at,recorded_at)
    values(a,'deal',d,'revert-deal-field','phase','{"phase":"legal"}','{"phase":"closing"}','automation_job','2026-10-06','2026-10-07') returning id into e;
  select * into row from v_deal_room_phase_change where deal_id=d;
  if not found or row.event_id is distinct from e or row.automatic is distinct from false
     or row.prior_phase is distinct from 'legal' or row.phase is distinct from 'closing'
     or row.reason is distinct from 'phase changed'
     or row.evidence_date::timestamptz is distinct from '2026-10-06'::timestamptz
     or row.recorded_at::timestamptz is distinct from '2026-10-07'::timestamptz
  then raise exception 'Undo identity, value or classification lost'; end if;
  update deal set invoiced_on='2026-10-06' where id=d;
  if exists(select 1 from v_deal_room_board where id=d) then raise exception 'Invoiced deal still on board'; end if;
end $$;
set local role carr_reader;
select id,operating_state,parking_note,invoiced_on from v_deal_room_board limit 1;
select event_id,prior_phase,reason,evidence_date from v_deal_room_phase_change limit 1;
reset role;
set local role carr_writer;
select event_id from v_deal_room_phase_change limit 1;
rollback;
