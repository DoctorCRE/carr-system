\set ON_ERROR_STOP on
begin;

insert into actor (id,slug,kind,display_name) values
 ('b1900000-0000-4000-8000-000000000001','codex-continuity-proof','automation','Continuity proof');

set local role carr_writer;
insert into codex_continuity_checkpoint
 (id,organization_tenant_id,owner_actor_id,native_task_id,project_id,cwd,state)
values
 ('b1900000-0000-4000-8000-000000000002','continuity-proof',
  'b1900000-0000-4000-8000-000000000001','native-proof','repo-proof','/proof',
  '{"objective":"preserve correction","next_action":"verify receipt"}');

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
end $$;
reset role;

do $$
begin
  if has_table_privilege('carr_reader','codex_continuity_checkpoint','INSERT') then
    raise exception 'reader unexpectedly has checkpoint write access';
  end if;
  if (select state->>'next_action' from codex_continuity_checkpoint
       where id='b1900000-0000-4000-8000-000000000002') is distinct from 'verify receipt' then
    raise exception 'checkpoint content was lost';
  end if;
end $$;

insert into claude_continuity_leaf
 (id,organization_tenant_id,surface_principal_actor_id,owner_actor_id,
  session_id,transcript_path_digest,project_affinity)
values
 ('b1900000-0000-4000-8000-000000000005','continuity-proof',
  'b1900000-0000-4000-8000-000000000001','b1900000-0000-4000-8000-000000000001',
  'claude-proof',repeat('a',64),'repo-proof');
set local role carr_writer;
insert into claude_continuity_checkpoint
 (leaf_id,state,cursor,source_observed_at)
values ('b1900000-0000-4000-8000-000000000005','{"objective":"retain checkpoint"}','{}',now());
update claude_continuity_checkpoint set checkpoint_version=2,
 state='{"objective":"checkpoint updated"}'
 where leaf_id='b1900000-0000-4000-8000-000000000005';
reset role;

do $$
declare retired text;
begin
  foreach retired in array array[
    'public.claude_continuity_event','public.codex_continuity_event',
    'public.claude_continuity_revision','public.codex_continuity_revision',
    'ops.retired_build_plan_review','ops.retired_build_admission_revision',
    'public.investigation_evidence',
    'public.sensitive_blob','public.search_candidate','public.actor_profile',
    'public.ammo_item','ops.workflow_cutover_retire_receipt_use'
  ] loop
    if to_regclass(retired) is not null then
      raise exception 'orphan table survived retirement: %', retired;
    end if;
  end loop;
  if (select state->>'objective' from claude_continuity_checkpoint
       where leaf_id='b1900000-0000-4000-8000-000000000005') is distinct from 'checkpoint updated' then
    raise exception 'Claude checkpoint update was lost';
  end if;
  if to_regclass('public.event') is null then
    raise exception 'continuity audit mirror was removed';
  end if;
end $$;

rollback;
