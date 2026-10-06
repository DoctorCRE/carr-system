\set ON_ERROR_STOP on
begin;

insert into actor (id,slug,kind,display_name) values
 ('c1850000-0000-4000-8000-000000000001','claude-continuity-proof','automation','Claude continuity proof'),
 ('c1850000-0000-4000-8000-000000000002','claude-continuity-owner-proof','human','Claude continuity owner proof');

set local role carr_writer;
insert into claude_continuity_leaf
 (id,organization_tenant_id,surface_principal_actor_id,owner_actor_id,session_id,
  transcript_path_digest,project_affinity,parent_session_id,native_agent_id,latest_cwd,latest_model_id)
values
 ('c1850000-0000-4000-8000-000000000003','claude-proof',
  'c1850000-0000-4000-8000-000000000001','c1850000-0000-4000-8000-000000000002',
  'session-proof',repeat('a',64),'sha256:project','parent-proof','agent-proof','/proof','claude-fable-5-1');
insert into claude_continuity_checkpoint
 (id,leaf_id,state,cursor,source_observed_at,compaction_generation)
values
 ('c1850000-0000-4000-8000-000000000004','c1850000-0000-4000-8000-000000000003',
  '{"objective":"preserve corrections","next_action":"verify receipt","source_observed_at":"2026-09-06T12:00:00Z","source_cursor":{"byte_offset":10}}',
  '{"byte_offset":10}','2026-09-06T12:00:00Z',2);
insert into claude_continuity_revision
 (checkpoint_id,checkpoint_version,state,cursor,source_observed_at,compaction_generation,created_by_actor_id)
select id,checkpoint_version,state,cursor,source_observed_at,compaction_generation,
 'c1850000-0000-4000-8000-000000000001' from claude_continuity_checkpoint
 where id='c1850000-0000-4000-8000-000000000004';
insert into claude_continuity_event
 (organization_tenant_id,surface_principal_actor_id,leaf_id,event_type,cursor,observed_at,idempotency_key)
values ('claude-proof','c1850000-0000-4000-8000-000000000001',
 'c1850000-0000-4000-8000-000000000003','pre_compact','{"byte_offset":10}',
 '2026-09-06T12:00:00Z','proof-event');

do $$
declare changed integer; denied boolean;
begin
  update claude_continuity_checkpoint set checkpoint_version=checkpoint_version+1,
    compaction_generation=3 where id='c1850000-0000-4000-8000-000000000004' and checkpoint_version=1;
  get diagnostics changed=row_count;
  if changed<>1 then raise exception 'current version update failed'; end if;
  update claude_continuity_checkpoint set checkpoint_version=checkpoint_version+1
    where id='c1850000-0000-4000-8000-000000000004' and checkpoint_version=1;
  get diagnostics changed=row_count;
  if changed<>0 then raise exception 'stale version updated state'; end if;
  denied:=false;
  begin
    update claude_continuity_leaf set project_affinity='rebound'
      where id='c1850000-0000-4000-8000-000000000003';
  exception when raise_exception then denied:=true;
  end;
  if not denied then raise exception 'leaf binding was mutable'; end if;
  denied:=false;
  begin
    update claude_continuity_checkpoint set compaction_generation=1
      where id='c1850000-0000-4000-8000-000000000004';
  exception when raise_exception then denied:=true;
  end;
  if not denied then raise exception 'compaction generation regressed'; end if;
  denied:=false;
  begin
    update claude_continuity_event set event_type='stop' where idempotency_key='proof-event';
  exception when insufficient_privilege then denied:=true;
  end;
  if not denied then raise exception 'writer could rewrite lifecycle receipt'; end if;
end $$;
reset role;

do $$
declare denied boolean:=false;
begin
  begin
    update claude_continuity_revision set state='{}'
      where checkpoint_id='c1850000-0000-4000-8000-000000000004';
  exception when raise_exception then denied:=true;
  end;
  if not denied then raise exception 'revision was not append-only'; end if;
  if has_table_privilege('carr_reader','claude_continuity_checkpoint','INSERT') then
    raise exception 'reader unexpectedly has checkpoint write access';
  end if;
end $$;

rollback;
