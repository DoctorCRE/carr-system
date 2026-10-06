\set ON_ERROR_STOP on
begin;

-- The disposable migration lane applies the schema before this handler fixture.

insert into actor (id,slug,kind,display_name) values
 ('b1900000-0000-4000-8000-000000000001','codex-continuity-proof','automation','Continuity proof');

set local role carr_writer;
insert into codex_continuity_checkpoint
 (id,organization_tenant_id,owner_actor_id,native_task_id,project_id,cwd,state)
values
 ('b1900000-0000-4000-8000-000000000002','continuity-proof',
  'b1900000-0000-4000-8000-000000000001','native-proof','repo-proof','/proof',
  '{"objective":"preserve correction","next_action":"verify receipt"}');
insert into codex_continuity_revision
 (checkpoint_id,checkpoint_version,state,created_by_actor_id)
select id,checkpoint_version,state,owner_actor_id from codex_continuity_checkpoint
 where id='b1900000-0000-4000-8000-000000000002';
insert into codex_continuity_event
 (organization_tenant_id,owner_actor_id,native_task_id,project_id,cwd,event_type,idempotency_key)
values ('continuity-proof','b1900000-0000-4000-8000-000000000001',
 'native-proof','repo-proof','/proof','pre_compact','proof-event');

do $$
declare denied boolean:=false;
begin
  if (select reference_manifest from codex_continuity_checkpoint
       where id='b1900000-0000-4000-8000-000000000002') <> '{}'::jsonb then
    raise exception 'legacy checkpoint did not retain the empty manifest default';
  end if;
  begin
    insert into codex_continuity_checkpoint
      (id,organization_tenant_id,owner_actor_id,native_task_id,project_id,cwd,state,reference_manifest)
    values
      ('b1900000-0000-4000-8000-000000000004','continuity-proof',
       'b1900000-0000-4000-8000-000000000001','native-manifest-over-cap','repo-proof','/proof',
       '{"objective":"bounded","next_action":"reject excess manifest"}',
       jsonb_build_object('version',1,'entries',jsonb_build_array(repeat('x',128000))));
  exception when check_violation then denied:=true;
  end;
  if not denied then raise exception 'reference manifest exceeded its 128KB cap'; end if;
end $$;

do $$
declare changed integer; denied boolean;
begin
  update codex_continuity_checkpoint set checkpoint_version=checkpoint_version+1
   where id='b1900000-0000-4000-8000-000000000002' and checkpoint_version=1;
  get diagnostics changed=row_count;
  if changed<>1 then raise exception 'current version update failed'; end if;
  update codex_continuity_checkpoint set checkpoint_version=checkpoint_version+1
   where id='b1900000-0000-4000-8000-000000000002' and checkpoint_version=1;
  get diagnostics changed=row_count;
  if changed<>0 then raise exception 'stale version updated state'; end if;
  denied:=false;
  begin
    update codex_continuity_checkpoint set project_id='rebound'
     where id='b1900000-0000-4000-8000-000000000002';
  exception when raise_exception then denied:=true;
  end;
  if not denied then raise exception 'project binding was mutable'; end if;
  denied:=false;
  begin
    update codex_continuity_checkpoint set cwd='/rebound'
     where id='b1900000-0000-4000-8000-000000000002';
  exception when raise_exception then denied:=true;
  end;
  if not denied then raise exception 'cwd binding was mutable'; end if;
  denied:=false;
  begin
    update codex_continuity_event set event_type='rewritten'
     where idempotency_key='proof-event';
  exception when insufficient_privilege then denied:=true;
  end;
  if not denied then raise exception 'writer could rewrite lifecycle receipts'; end if;
end $$;
reset role;

-- Even a table owner cannot silently rewrite a saved revision.
do $$
declare denied boolean:=false;
begin
  begin
    update codex_continuity_revision set state='{}'
     where checkpoint_id='b1900000-0000-4000-8000-000000000002';
  exception when raise_exception then denied:=true;
  end;
  if not denied then raise exception 'revision was not append-only'; end if;
  if has_table_privilege('carr_reader','codex_continuity_checkpoint','INSERT') then
    raise exception 'reader unexpectedly has checkpoint write access';
  end if;
  if (select state->>'next_action' from codex_continuity_checkpoint
       where id='b1900000-0000-4000-8000-000000000002')<>'verify receipt' then
    raise exception 'checkpoint content was lost';
  end if;
end $$;

rollback;
