-- V5-R03 producer proof. All rows are synthetic and rolled back.
\set ON_ERROR_STOP on
begin;

do $r03_order$
declare v_733 timestamptz; v_734 timestamptz; v_735 timestamptz;
begin
  select applied_at into v_733 from public.schema_migrations
   where filename='0733_salesforce_rw02_safe_stop_run_store.sql';
  select applied_at into v_734 from public.schema_migrations
   where filename='0734_salesforce_rw02_safe_stop_scac_successor.sql';
  select applied_at into v_735 from public.schema_migrations
   where filename='0735_calendar_prebrief_skip_unknown_attendees.sql';
  if v_733 is null or v_734 is null or v_735 is null or
     v_733 > v_734 or v_734 > v_735 then
    raise exception 'R03 fixture requires an ordered predecessor ledger';
  end if;
end $r03_order$;

do $r03_fixture$
declare v_joe uuid; v_dell uuid; v_claude uuid; v_block uuid;
        v_seq integer; v_action uuid; v_self uuid; v_future uuid;
        v_dell_action uuid; v_writer_action uuid; v_failed_action uuid;
        v_count integer;
begin
  select id into strict v_joe from public.actor where slug='joe' and kind='human';
  select id into strict v_dell from public.actor where slug='dell' and kind='human';
  select id into strict v_claude from public.actor where slug='claude' and kind='automation';
  insert into public.loop_block
    (rel_path,kind,seq,block_key,col_order,created_by,updated_by)
  values ('__r03_synthetic_queue__','action_required',1,'open',
          array['number','title','owner'],v_claude,v_claude)
  returning id into v_block;
  v_seq := 1;

  insert into public.loop_item
    (kind,number,block_id,render_seq,title,owner,marker,tier,created_by,updated_by)
  values ('action_required','R03-SYNTH-1',v_block,v_seq,'Synthetic action','joe',
          'none','shared',v_claude,v_claude) returning id into v_action;
  insert into public.loop_item
    (kind,number,block_id,render_seq,title,owner,marker,tier,created_by,updated_by)
  values ('action_required','R03-SYNTH-2',v_block,v_seq+1,'Synthetic self action','joe',
          'none','shared',v_joe,v_joe) returning id into v_self;
  insert into public.loop_item
    (kind,number,block_id,render_seq,title,owner,marker,due_on,tier,created_by,updated_by)
  values ('action_required','R03-SYNTH-3',v_block,v_seq+2,'Synthetic dated action','joe',
          'dated',current_date+7,'shared',v_claude,v_claude) returning id into v_future;
  insert into public.loop_item
    (kind,number,block_id,render_seq,title,owner,marker,tier,created_by,updated_by)
  values ('action_required','R03-SYNTH-4',v_block,v_seq+3,'Synthetic Dell action','dell',
          'none','shared',v_claude,v_claude) returning id into v_dell_action;
  insert into public.loop_item
    (kind,number,block_id,render_seq,title,owner,marker,tier,created_by,updated_by)
  values ('action_required','R03-SYNTH-5',v_block,v_seq+4,'Synthetic writer action','joe',
          'none','shared',v_claude,v_claude) returning id into v_writer_action;
  insert into public.loop_item
    (kind,number,block_id,render_seq,title,owner,marker,tier,created_by,updated_by)
  values ('action_required','R03-SYNTH-6',v_block,v_seq+5,'Synthetic failed mint','joe',
          'none','shared',v_claude,v_claude) returning id into v_failed_action;
  perform set_config('r03.writer_action_id',v_writer_action::text,true);
  perform set_config('r03.failed_action_id',v_failed_action::text,true);
  perform set_config('r03.block_id',v_block::text,true);
  perform set_config('r03.claude_actor_id',v_claude::text,true);

  -- Keep the synthetic device lane inside a quiet window. The producer uses
  -- the existing mint's quiet-hour decision with no bypass or morning hold.
  insert into ops.notification_preference
    (actor,device_opt_in,quiet_hours_start,quiet_hours_end,timezone)
  values (v_joe,true,
          ((now() at time zone 'UTC')::time - interval '1 hour')::time,
          ((now() at time zone 'UTC')::time + interval '1 hour')::time,'UTC')
  on conflict (actor) do update set
    device_opt_in=excluded.device_opt_in,
    quiet_hours_start=excluded.quiet_hours_start,
    quiet_hours_end=excluded.quiet_hours_end,
    timezone=excluded.timezone;

  insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,new_value,cause)
  values (now(),v_claude,'add-loop','loop',v_action,
          jsonb_build_object('owner','joe','kind','action_required'),'system');
  select count(*) into v_count from ops.notification n
   where n.subject_type='loop' and n.subject_ref=v_action::text
     and n.recipient_actor=v_joe;
  if v_count<>1 then raise exception 'eligible Joe action minted % rows',v_count; end if;
  if not exists (select 1 from ops.notification n join ops.notification_delivery d
                  on d.notification_id=n.id and d.channel='in_app'
                 where n.subject_ref=v_action::text and n.recipient_actor=v_joe) then
    raise exception 'eligible Joe action lacks an in-app delivery row';
  end if;
  if not exists (select 1 from ops.notification n join ops.notification_delivery d
                  on d.notification_id=n.id and d.channel='device'
                 where n.subject_ref=v_action::text and n.recipient_actor=v_joe
                   and d.state='suppressed_quiet_hours') then
    raise exception 'Joe device row bypassed synthetic quiet hours';
  end if;

  -- A repeated source act must reuse the same recipient/key, never mint a
  -- second notification. The metadata event distinguishes that result.
  insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,new_value,cause)
  values (now(),v_claude,'add-loop','loop',v_action,
          jsonb_build_object('owner','joe','kind','action_required'),'system');
  select count(*) into v_count from ops.notification n
   where n.subject_type='loop' and n.subject_ref=v_action::text;
  if v_count<>1 then raise exception 'repeated action minted % rows',v_count; end if;
  if not exists (select 1 from public.event a
                  where a.subject_type='loop' and a.subject_id=v_action
                    and a.verb='loop-notification-attempt'
                    and a.new_value->>'recipient_actor'=v_joe::text
                    and a.new_value->>'outcome'='deduplicated') then
    raise exception 'repeated action has no dedupe receipt';
  end if;

  insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,new_value,cause)
  values (now(),v_joe,'add-loop','loop',v_self,
          jsonb_build_object('owner','joe','kind','action_required'),'human_stated'),
         (now(),v_claude,'add-loop','loop',v_future,
          jsonb_build_object('owner','joe','kind','action_required'),'system'),
         (now(),v_claude,'add-loop','loop',v_dell_action,
          jsonb_build_object('owner','dell','kind','action_required'),'system');
  select count(*) into v_count from ops.notification n
   where n.subject_type='loop' and
     n.subject_ref in (v_self::text,v_future::text);
  if v_count<>0 then raise exception 'self/future action minted % rows',v_count; end if;
  select count(*) into v_count from ops.notification n
   where n.subject_type='loop' and n.subject_ref=v_dell_action::text
     and n.recipient_actor=v_dell;
  if v_count<>1 then raise exception 'eligible Dell action minted % rows',v_count; end if;
  select count(*) into v_count from ops.notification n
   where n.subject_ref in (v_action::text,v_dell_action::text)
     and n.recipient_actor not in (v_joe,v_dell);
  if v_count<>0 then raise exception 'action routed to the wrong actor'; end if;
end $r03_fixture$;

-- add-loop writes public.event as carr_writer. Prove the actual role can fire
-- the definer trigger, while direct entry to the wrapper remains revoked.
do $r03_grant$
begin
  if not has_table_privilege('carr_writer','public.event','insert') or
     has_function_privilege('carr_writer','ops.enqueue_loop_notification(uuid)','execute') then
    raise exception 'R03 writer/trigger grant boundary changed';
  end if;
end $r03_grant$;
set session authorization carr_writer;
do $r03_session$
begin
  if session_user <> 'carr_writer' or current_user <> 'carr_writer' then
    raise exception 'R03 writer identity is not the actual session';
  end if;
end $r03_session$;
insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,new_value,cause)
values (now(),current_setting('r03.claude_actor_id')::uuid,'add-loop','loop',
        current_setting('r03.writer_action_id')::uuid,
        jsonb_build_object('owner','joe','kind','action_required'),'system');
reset session authorization;
do $r03_writer_result$
declare v_count integer;
begin
  select count(*) into v_count from ops.notification n
   where n.subject_type='loop' and
         n.subject_ref=current_setting('r03.writer_action_id');
  if v_count<>1 then raise exception 'writer-trigger action minted % rows',v_count; end if;
end $r03_writer_result$;

-- A failed notification insert must not roll back the admitted source event.
-- The only failure receipt is sanitized metadata in the existing event log.
create function ops.r03_synthetic_mint_refusal()
returns trigger language plpgsql as $$
begin
  raise exception 'synthetic mint refusal';
end $$;
create trigger r03_synthetic_mint_refusal before insert on ops.notification
for each row execute function ops.r03_synthetic_mint_refusal();
set session authorization carr_writer;
insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,new_value,cause)
values (now(),current_setting('r03.claude_actor_id')::uuid,'add-loop','loop',
        current_setting('r03.failed_action_id')::uuid,
        jsonb_build_object('owner','joe','kind','action_required'),'system');
reset session authorization;
drop trigger r03_synthetic_mint_refusal on ops.notification;
drop function ops.r03_synthetic_mint_refusal();
do $r03_failure_result$
begin
  if not exists (select 1 from public.event
                  where subject_type='loop' and verb='add-loop'
                    and subject_id=current_setting('r03.failed_action_id')::uuid) then
    raise exception 'failed mint rolled back its source event';
  end if;
  if not exists (select 1 from public.event
                  where subject_type='loop' and verb='loop-notification-attempt'
                    and subject_id=current_setting('r03.failed_action_id')::uuid
                    and new_value->>'outcome'='failed'
                    and new_value->>'failure_code'='P0001') then
    raise exception 'failed mint lacks sanitized failure receipt';
  end if;
end $r03_failure_result$;

-- Retry the same eligible action after the synthetic mint refusal is gone.
-- Its historical failure remains in the event log, but in-app delivery now
-- resolves the active product gap.
set session authorization carr_writer;
insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,new_value,cause)
values (now(),current_setting('r03.claude_actor_id')::uuid,'add-loop','loop',
        current_setting('r03.failed_action_id')::uuid,
        jsonb_build_object('owner','joe','kind','action_required'),'system');
reset session authorization;
do $r03_recovery_result$
begin
  if not exists (select 1 from ops.notification n
                  join ops.notification_delivery d on d.notification_id=n.id
                   and d.channel='in_app'
                  where n.subject_type='loop'
                    and n.subject_ref=current_setting('r03.failed_action_id')) then
    raise exception 'retry did not restore in-app delivery';
  end if;
  if not exists (select 1 from public.event
                  where subject_type='loop' and verb='loop-notification-attempt'
                    and subject_id=current_setting('r03.failed_action_id')::uuid
                    and new_value->>'outcome'='failed') then
    raise exception 'recovery erased historical failure evidence';
  end if;
end $r03_recovery_result$;
select 'r03_recovered_health' as phase;
\i ops/r03-human-queue-health.sql

-- A mismatched actor is not a valid creation event or mint source.
do $r03_bad_actor_fixture$
declare v_action uuid; v_claude uuid;
begin
  select id into strict v_claude from public.actor
   where slug='claude' and kind='automation';
  insert into public.loop_item
    (kind,number,block_id,render_seq,title,owner,marker,tier,created_by,updated_by)
  values ('action_required','R03-SYNTH-7',current_setting('r03.block_id')::uuid,
          7,'Synthetic actor mismatch','joe','none','shared',v_claude,v_claude)
  returning id into v_action;
  perform set_config('r03.bad_actor_action_id',v_action::text,true);
end $r03_bad_actor_fixture$;
insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,new_value,cause)
values (now(),(select id from public.actor where slug='joe' and kind='human'),
        'add-loop','loop',current_setting('r03.bad_actor_action_id')::uuid,
        jsonb_build_object('owner','joe','kind','action_required'),'system');
do $r03_actor_result$
begin
  if exists (select 1 from ops.notification
              where subject_type='loop'
                and subject_ref=current_setting('r03.bad_actor_action_id')) then
    raise exception 'mismatched actor minted notification';
  end if;
end $r03_actor_result$;

-- Execute the same standing query after adding the actor mismatch.
select 'r03_actor_mismatch_health' as phase;
\i ops/r03-human-queue-health.sql

rollback;
select 'V5-R03 synthetic loop producer and dedupe proof passed' as proof;
