-- Actual session_user=dot_reader calls, never SET ROLE or a read-only
-- transaction (either would conceal a SECURITY DEFINER write escape).
-- Fixtures and every successful call roll back. No production data is used.
\set ON_ERROR_STOP on
-- WR-000258: the typed calls below are independent, and each pays for a full
-- hash of every public/ops row (the sealed SCAC registry alone is ~240k rows),
-- so the CI runner splits them across disposable database copies. Run as
-- `-v shards=N -v shard=K` each session executes only calls whose position in
-- signature order is K modulo N; every session still builds the whole fixture,
-- the positive control and every other assertion. With neither variable set
-- (the default, and what a human typing psql gets) one session runs all calls.
\if :{?shards}
\else
\set shards 1
\set shard 0
\endif
begin;
select set_config('dot.shards', :'shards', true), set_config('dot.shard', :'shard', true);
set local search_path = pg_catalog;

create temp table dot_definer_calls(signature text primary key, arguments text not null,
                                  writing boolean not null default false);
insert into dot_definer_calls(signature,arguments,writing) values
 ('public.memory_item_insert_valid()', '', false),
 ('public.memory_item_plan_anchor_valid()', '', false),
 ('ops.attempt_receipt_binding_valid()', '', false),
 ('ops.bind_execution_environment_to_assignment()', '', true),
 ('ops.bind_execution_environment_to_envelope()', '', false),
 ('ops.engineering_admission_source(text)', $$'WR-DOT-SYNTHETIC'::text$$, false),
 ('ops.engineering_passport_facts(text)', $$'WR-DOT-SYNTHETIC'::text$$, false),
 ('ops.engineering_register_slice_plan(text,jsonb,text,uuid)',
  $$'WR-DOT-SYNTHETIC'::text,'{}'::jsonb,'sha256:synthetic'::text,'00000000-0000-4000-8000-000000000001'::uuid$$, true),
 ('ops.fence_jobs_when_definition_disabled()', '', true),
 ('ops.foundation_assurance_accepted_manifest(uuid)', $$'00000000-0000-4000-8000-000000000001'::uuid$$, false),
 ('ops.foundation_assurance_subject_maker(uuid)', $$'00000000-0000-4000-8000-000000000001'::uuid$$, false),
 ('ops.hermes_runtime_admission_for_brief(text,text,text,text,text)',
  $$'synthetic-runtime'::text,'synthetic-profile'::text,'synthetic-sponsor'::text,'WR-DOT-SYNTHETIC'::text,'synthetic-binding'::text$$, false),
 ('ops.issue_execution_envelope_v1(text,text,uuid)',
  $$'WR-DOT-SYNTHETIC'::text,'synthetic-binding'::text,'00000000-0000-4000-8000-000000000001'::uuid$$, true),
 ('ops.read_governance_queue()', '', false),
 ('ops.refuse_direct_rule_delivery_policy_update()', '', false),
 ('ops.release_schema_declaration_matches_live()', '', false),
 ('ops.require_rule_approval_lifecycle_anchor()', '', false),
 ('ops.rule_delivery_plan(text,text[])', $$'synthetic-sponsor'::text,array['engineering-git']::text[]$$, false),
 ('ops.rule_pack_index()', '', false),
 ('ops.sourced_work_request_is_immutable()', '', false),
 ('ops.sourced_work_shape_receipts_are_immutable()', '', false),
 ('ops.validate_attempt_environment_evidence()', '', false),
 ('ops.work_request_card(text,text)', $$'WR-DOT-SYNTHETIC'::text,'carr-internal'::text$$, false),
 ('ops.work_request_withdrawal_receipt_append_only()', '', false);
grant select on dot_definer_calls to dot_reader;

-- Base tables, partitioned tables and sequences, not views whose own function
-- calls could confound the measurement. Sorted row hashes catch UPDATE and
-- delete+insert substitutions as well as row-count changes. Sequence state
-- catches nextval/setval even when the surrounding statement rolls back.
create function pg_temp.dot_relation_state() returns jsonb language plpgsql as $state$
declare relation record; fingerprint text; result jsonb := '{}'::jsonb;
begin
  for relation in
    select n.nspname,c.relname,c.relkind from pg_class c
    join pg_namespace n on n.oid=c.relnamespace
    where n.nspname in ('public','ops') and c.relkind in ('r','p','S')
    order by n.nspname,c.relname
  loop
    if relation.relkind='S' then
      execute format('select md5(jsonb_build_object(''last_value'',last_value,''is_called'',is_called)::text) from %I.%I',
        relation.nspname,relation.relname) into fingerprint;
    else
      execute format('select md5(coalesce(string_agg(md5(to_jsonb(t)::text), %L order by md5(to_jsonb(t)::text)), %L)) from %I.%I t',
        ',', '', relation.nspname,relation.relname) into fingerprint;
    end if;
    result := result || jsonb_build_object(relation.nspname||'.'||relation.relname,fingerprint);
  end loop;
  return result;
end $state$;
grant execute on function pg_temp.dot_relation_state() to dot_reader;

-- Positive control: an UPDATE with no row-count change must be observable.
-- The intentionally unsafe definer is session-local, outside public/ops.
create table ops.dot_definer_write_probe(value integer not null);
insert into ops.dot_definer_write_probe values (1);
grant select on ops.dot_definer_write_probe to dot_reader;
create function pg_temp.dot_unsafe_writer() returns void language sql security definer
  set search_path=pg_catalog,pg_temp
  as $$update ops.dot_definer_write_probe set value=2$$;
grant execute on function pg_temp.dot_unsafe_writer() to dot_reader;
create temp table dot_before_control as select pg_temp.dot_relation_state() state;
grant select on dot_before_control to dot_reader;
savepoint positive_control;
set session authorization dot_reader;
select pg_temp.dot_unsafe_writer();
do $control$
begin
  if pg_temp.dot_relation_state() = (select state from pg_temp.dot_before_control) then
    raise exception 'write detector missed the synthetic SECURITY DEFINER UPDATE';
  end if;
end $control$;
reset session authorization;
rollback to positive_control;

-- Revoking direct EXECUTE must preserve existing writer-trigger behavior.
-- This synthetic table supplies the same OLD/NEW shape as the definition
-- trigger, while a synthetic key matches no queued jobs in this owned DB.
create table ops.dot_definer_trigger_probe(key text,version integer,enabled boolean);
insert into ops.dot_definer_trigger_probe values ('dot-synthetic-definition',1,true);
create trigger dot_definer_trigger_probe after update on ops.dot_definer_trigger_probe
  for each row execute function ops.fence_jobs_when_definition_disabled();
grant select on ops.dot_definer_trigger_probe to dot_reader;
grant select,update on ops.dot_definer_trigger_probe to carr_writer;
set local role carr_writer;
update ops.dot_definer_trigger_probe set enabled=false;
reset role;
do $trigger_use$
begin
  if (select enabled from ops.dot_definer_trigger_probe) then
    raise exception 'writer-trigger behavior did not survive ACL hardening';
  end if;
end $trigger_use$;

-- Verify the complete current catalog is covered. A newly exposed definer
-- requires a reviewed typed call; it cannot silently disappear from this proof.
do $coverage$
declare uncovered text;
begin
  select string_agg(p.oid::regprocedure::text,', ')
    into uncovered from pg_proc p join pg_namespace n on n.oid=p.pronamespace
   where n.nspname in ('public','ops') and p.prosecdef
     and has_function_privilege('dot_reader',p.oid,'EXECUTE')
     and not exists(select 1 from pg_temp.dot_definer_calls c
                    where to_regprocedure(c.signature)=p.oid);
  if uncovered is not null then raise exception 'Dot executable definers lack typed fixtures: %',uncovered; end if;
  if exists(select 1 from pg_temp.dot_definer_calls c
            left join pg_proc p on p.oid=to_regprocedure(c.signature)
            where p.oid is null or not p.prosecdef) then
    raise exception 'reviewed Dot definer disappeared or changed security mode';
  end if;
  -- The scope being measured includes inherited privileges, not only ACL text.
  if exists(select 1 from pg_roles r where r.rolname<>'dot_reader'
            and pg_has_role('dot_reader',r.oid,'USAGE')) then
    raise exception 'Dot acquired an inherited privilege bundle; review its write doors';
  end if;
end $coverage$;

set session authorization dot_reader;
do $calls$
declare call record; before_state jsonb; after_state jsonb; refusal text; changed text;
  shards integer := current_setting('dot.shards')::integer;
  shard integer := current_setting('dot.shard')::integer;
begin
  if session_user<>'dot_reader' or current_user<>'dot_reader' then
    raise exception 'proof must use the Dot session identity';
  end if;
  perform set_config('carr.organization_tenant_id','carr-internal',true);
  -- Machine-read by ops/ci-migration-gates.py (scenario_coverage): the shards
  -- together must run every reviewed call exactly once.
  raise notice 'gate-scenarios-declared: %',
    (select string_agg(signature,' ' order by signature) from pg_temp.dot_definer_calls);
  if shards<1 or shard<0 or shard>=shards then
    raise exception 'invalid Dot call shard %/%',shard,shards;
  end if;
  -- One baseline for the whole session. The loop below requires every call to
  -- leave the relation state exactly as it found it, so each call's "before"
  -- is this baseline; hashing it again per call only repeated the same scan.
  before_state := pg_temp.dot_relation_state();
  for call in select * from (
                select c.*,p.prorettype,
                       (row_number() over (order by c.signature))-1 as position
                  from pg_temp.dot_definer_calls c
                  join pg_proc p on p.oid=to_regprocedure(c.signature)) numbered
              where numbered.position % shards = shard order by numbered.signature
  loop
    refusal := null;
    begin
      execute format('select %s(%s)',split_part(call.signature,'(',1),call.arguments);
    exception
      when insufficient_privilege then refusal := sqlstate;
      when feature_not_supported then
        if call.prorettype<>'trigger'::regtype then raise; end if;
        refusal := sqlstate;
    end;
    after_state := pg_temp.dot_relation_state();
    if after_state is distinct from before_state then
      select string_agg(key,', ' order by key) into changed
        from jsonb_each(after_state) where value is distinct from before_state->key;
      raise exception 'Dot changed relation state through %: %',call.signature,changed;
    end if;
    raise notice 'gate-scenario-ran: %',call.signature;
    raise notice 'Dot call %: %; table and sequence state unchanged',
      call.signature,coalesce('refused '||refusal,'returned');
  end loop;
end $calls$;
reset session authorization;

-- A body-level guard is useful defense in depth, but does not justify ambient
-- EXECUTE for a read-only login. This assertion is separately red before ACL
-- hardening even when all behavioral calls already refuse or return read data.
do $writer_acl$
declare exposed text;
begin
  select string_agg(signature,', ' order by signature) into exposed
    from pg_temp.dot_definer_calls where writing
      and has_function_privilege('dot_reader',to_regprocedure(signature),'EXECUTE');
  if exposed is not null then
    raise exception 'writing definers remain Dot-executable: %',exposed;
  end if;
end $writer_acl$;
rollback;
