-- Permanent catalog policy, including definers Dot cannot execute.
\set ON_ERROR_STOP on
begin;
set local search_path = pg_catalog;
create function pg_temp.assert_definer_paths() returns void language plpgsql as $guard$
declare routine record; path text; settings integer; role_name text; schema_name text;
begin
  for routine in
    select p.oid::regprocedure signature,n.nspname,p.proconfig
      from pg_proc p join pg_namespace n on n.oid=p.pronamespace
     where p.prosecdef and n.nspname not in ('pg_catalog','information_schema')
       and n.nspname !~ '^pg_(temp|toast)'
  loop
    -- Future application definer schemas require an explicit reviewed policy.
    if routine.nspname not in ('public','ops') then
      raise exception 'unreviewed definer schema: %',routine.signature;
    end if;
    select count(*),min(substring(setting from 13)) into settings,path
      from unnest(routine.proconfig) setting where setting like 'search_path=%';
    -- Exact trusted orders reject duplicate tokens, early pg_temp and $user.
    if settings<>1 or not (array_to_string(regexp_split_to_array(path,'\s*,\s*'),',') = any(array[
      'pg_catalog,ops,pg_temp','pg_catalog,ops,public,pg_temp',
      'pg_catalog,public,ops,pg_temp','ops,public,pg_temp','public,pg_temp',
      'pg_catalog,public,pg_temp','ops,pg_temp','pg_catalog,pg_temp'
    ])) then
      raise exception 'untrusted definer path: %: %',routine.signature,path;
    end if;
  end loop;
  -- Effective privileges include PUBLIC and inherited grants. The disposable
  -- owner is excluded; all application runtime roles must lack schema CREATE.
  for role_name in select rolname from pg_roles
     where not rolsuper and (rolname like 'carr\_%' escape '\' and rolname<>'carr_ci'
                            or rolname='dot_reader')
  loop
    foreach schema_name in array array['pg_catalog','public','ops'] loop
      if has_schema_privilege(role_name,schema_name,'CREATE') then
        raise exception 'runtime role can CREATE in trusted schema: %: %',role_name,schema_name;
      end if;
    end loop;
  end loop;
end $guard$;
select pg_temp.assert_definer_paths();

-- Negatives exercise the SAME guard, each independently rolled back.
savepoint duplicate_temp;
alter function public.retrieval_visibility_actor_id(text) set search_path=pg_temp,public,pg_temp;
do $$begin
  begin perform pg_temp.assert_definer_paths();
  exception when others then
    if sqlerrm like 'untrusted definer path:%' then return; end if; raise;
  end;
  raise exception 'guard accepted early duplicate pg_temp';
end$$;
rollback to duplicate_temp;

savepoint writable_schema;
create schema definer_attacker;
grant usage,create on schema definer_attacker to dot_reader;
alter function public.retrieval_visibility_actor_id(text) set search_path=definer_attacker,public,pg_temp;
do $$begin
  begin perform pg_temp.assert_definer_paths();
  exception when others then
    if sqlerrm like 'untrusted definer path:%' then return; end if; raise;
  end;
  raise exception 'guard accepted attacker-writable schema';
end$$;
rollback to writable_schema;

savepoint trusted_create;
grant create on schema public to dot_reader;
do $$begin
  begin perform pg_temp.assert_definer_paths();
  exception when others then
    if sqlerrm like 'runtime role can CREATE in trusted schema:%' then return; end if; raise;
  end;
  raise exception 'guard accepted runtime CREATE in public';
end$$;
rollback to trusted_create;

savepoint unknown_schema;
create schema definer_future;
create function definer_future.unreviewed() returns integer language sql security definer
  set search_path=pg_catalog,pg_temp as $$select 1$$;
do $$begin
  begin perform pg_temp.assert_definer_paths();
  exception when others then
    if sqlerrm like 'unreviewed definer schema:%' then return; end if; raise;
  end;
  raise exception 'guard accepted an unreviewed definer schema';
end$$;
rollback to unknown_schema;
select pg_temp.assert_definer_paths();
rollback;
