-- A caller's error-producing expression must not see another tenant's row.
\set ON_ERROR_STOP on
begin;
create temporary table tenant_observation_probe as
  select * from ops.completion_observation where false;
insert into tenant_observation_probe(id,organization_tenant_id,subject_id,
    observation_kind,source_kind,source_ref,observed_at) values
  ('10000000-0000-0000-0000-000000000001','barrier_allowed',
   '20000000-0000-0000-0000-000000000001','state','fixture','123',now()),
  ('10000000-0000-0000-0000-000000000002','barrier_hidden',
   '20000000-0000-0000-0000-000000000002','state','fixture','hidden_tenant_value',now());
do $clone$
declare definition text; options text[];
begin
  select pg_get_viewdef(c.oid,true),c.reloptions into definition,options
    from pg_class c where c.oid='ops.completion_current_observation'::regclass;
  definition:=replace(definition,'ops.completion_observation','pg_temp.tenant_observation_probe');
  execute 'create temporary view actual_view_probe '
    ||case when 'security_barrier=true'=any(options) then 'with (security_barrier=true) ' else '' end
    ||'as '||definition;
  execute 'create temporary view unsafe_view_probe as '||definition;
end $clone$;
grant select on actual_view_probe,unsafe_view_probe to carr_reader;
set session authorization carr_reader;
set local carr.organization_tenant_id='barrier_allowed';
do $probe$
declare actual text; leaked boolean:=false;
begin
  if session_user<>'carr_reader' then raise exception 'probe requires carr_reader session authorization'; end if;
  -- Positive control: prove the optimizer/error probe exercises the leak.
  begin
    perform source_ref from pg_temp.unsafe_view_probe where source_ref::integer>0;
  exception when invalid_text_representation then
    leaked:=position('hidden_tenant_value' in sqlerrm)>0;
  end;
  if not leaked then raise exception 'unsafe-view positive control did not expose hidden row'; end if;
  select source_ref into strict actual from pg_temp.actual_view_probe where source_ref::integer>0;
  if actual<>'123' then raise exception 'tenant barrier returned unexpected row: %',actual; end if;
end $probe$;
reset session authorization;
do $both_barriers$
declare view_name text;
begin
  foreach view_name in array array['ops.completion_current_observation','ops.completion_dimension_matrix'] loop
    if not exists(select 1 from pg_class where oid=view_name::regclass
      and 'security_barrier=true'=any(reloptions)) then
      raise exception 'tenant security barrier missing on %',view_name;
    end if;
  end loop;
end $both_barriers$;
rollback;
