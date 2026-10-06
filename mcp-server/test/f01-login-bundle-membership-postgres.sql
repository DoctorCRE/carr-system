\set ON_ERROR_STOP on
-- 0732: the F01/J102/RW02 actor gate classifies the Worker's REAL logins by
-- bundle membership, and admits nobody else. Every earlier fixture reached the
-- bundles with SET SESSION AUTHORIZATION carr_writer, which makes session_user
-- the NOLOGIN bundle itself. That is how the literal-name refusal of app_writer
-- and app_reader reached production unseen. This proof authenticates as login
-- roles shaped like production's: app_writer in carr_writer, app_reader in
-- carr_reader. It then proves the negative cases around them. Everything runs
-- in one transaction that is rolled back, including the role creation. Every
-- record is synthetic.
begin;

do $roles$
begin
  if not exists (select 1 from pg_roles where rolname = 'app_writer') then
    create role app_writer login;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'app_reader') then
    create role app_reader login;
  end if;
end $roles$;
grant carr_writer to app_writer;
grant carr_reader to app_reader;
-- The negative cast. Each is a real login whose membership would pass a bare
-- pg_has_role() test, or that holds no bundle at all.
create role f01t_unrelated login;
create role f01t_exporter login;
grant carr_exporter to f01t_exporter;          -- reaches carr_reader through carr_exporter
create role f01t_owner_like login;
grant carr_writer to f01t_owner_like;
grant carr_reader to f01t_owner_like;
grant carr_authority to f01t_owner_like;       -- neondb_owner's shape, minus ownership
create role f01t_jobs_writer login;
grant carr_jobs to f01t_jobs_writer;
grant carr_writer to f01t_jobs_writer;

-- 1. The classifier itself, as the owner.
do $classify$
begin
  if ops.login_bundle_principal('app_writer') is distinct from 'carr_writer'
     or ops.login_bundle_principal('app_reader') is distinct from 'carr_reader'
     or ops.login_bundle_principal('carr_writer') is distinct from 'carr_writer'
     or ops.login_bundle_principal('carr_reader') is distinct from 'carr_reader' then
    raise exception 'f01 membership fixture: a runtime login or bundle did not classify';
  end if;
  if ops.login_bundle_principal('f01t_unrelated') is not null
     or ops.login_bundle_principal('f01t_exporter') is not null
     or ops.login_bundle_principal('f01t_owner_like') is not null
     or ops.login_bundle_principal('f01t_jobs_writer') is not null
     or ops.login_bundle_principal('neondb_owner') is not null
     or ops.login_bundle_principal('carr_jobs') is not null
     or ops.login_bundle_principal(session_user) is not null   -- the superuser running CI
     or ops.login_bundle_principal('f01t_no_such_role') is not null then
    raise exception 'f01 membership fixture: a role outside the runtime bundles classified as one';
  end if;
end $classify$;

-- The owner-shaped reader-deny probe (step 5) needs a writer EXECUTE on the
-- reader's side, granted here and rolled back with everything else.
grant execute on function ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text) to f01t_owner_like;

-- 2. app_writer: admitted as a sponsored writer, on the real doors.
set session authorization app_writer;
select set_config('carr.acting_actor_slug', 'synthetic-f01-writer', true);
select set_config('carr.sponsoring_human_slug', 'joe', true);
do $writer$
declare v jsonb;
begin
  if ops.f01_context_actor_slug() is distinct from 'synthetic-f01-writer' then
    raise exception 'f01 membership fixture: app_writer did not resolve its acting actor';
  end if;
  v := ops.f01_principal();
  if v ->> 'authorization_class' <> 'sponsored_agent' or (v ->> 'human')::boolean then
    raise exception 'f01 membership fixture: app_writer was not a sponsored, non-human principal: %', v;
  end if;
  perform ops.f01_read('current_policy', '{}'::jsonb);
  perform ops.j102_read('first_party_record', '{}'::jsonb);
  if ops.j102_sponsoring_partner() is distinct from 'joe' then
    raise exception 'f01 membership fixture: app_writer lost its server-set sponsor';
  end if;
  if ops.rw02_replay('record-salesforce-write-readback', 'f01t-no-such-key',
       'sha256:0000000000000000000000000000000000000000000000000000000000000000') is not null then
    raise exception 'f01 membership fixture: rw02_replay invented a replay for app_writer';
  end if;
  -- No exception handler: any refusal on these doors fails the proof.
end $writer$;

-- The two 0344 writers get past their principal gate and fail later on the
-- synthetic, empty payload, never at the gate.
do $writer_0344$
begin
  begin
    perform ops.register_execution_environment_provider('{}'::jsonb, gen_random_uuid());
  exception when others then
    if sqlerrm like '%requires the authority connection or a sponsored writer session%' then
      raise exception 'f01 membership fixture: app_writer refused by register_execution_environment_provider';
    end if;
  end;
  begin
    perform ops.transition_proposed_eval_candidate('WR-000000', 'f01t-none', 'accepted', '{}'::jsonb, gen_random_uuid());
  exception when others then
    if sqlerrm like '%requires the authority connection or a sponsored writer session%' then
      raise exception 'f01 membership fixture: app_writer refused by transition_proposed_eval_candidate';
    end if;
  end;
end $writer_0344$;

-- Without the server-set acting actor, the writer is still refused.
select set_config('carr.acting_actor_slug', '', true);
do $writer_no_actor$
begin
  perform ops.f01_context_actor_slug();
  raise exception 'f01 membership fixture: app_writer resolved an actor with none set';
exception when others then
  if sqlerrm not like '%f01_no_authenticated_actor%' then raise; end if;
end $writer_no_actor$;
reset session authorization;

-- 3. app_reader: admitted as the fixed read-only principal, never as an actor
-- it names for itself, and never able to write.
set session authorization app_reader;
select set_config('carr.acting_actor_slug', 'joe', true);
do $reader$
begin
  if ops.f01_context_actor_slug() is distinct from 'carr-reader' then
    raise exception 'f01 membership fixture: app_reader did not resolve to carr-reader';
  end if;
  perform ops.f01_read('current_policy', '{}'::jsonb);
  perform ops.j102_read('first_party_record', '{}'::jsonb);
  -- No exception handler: any refusal on these doors fails the proof.
end $reader$;
do $reader_cannot_write$
declare fn text;
begin
  foreach fn in array array[
    'select ops.f01_record_document(''{}''::jsonb,''{}''::jsonb,''{}''::jsonb,''k'',''d'',''x'')',
    'select ops.f01_register_derivative_link(''{}''::jsonb,''k'',''d'')',
    'select ops.f01_record_artifact(''{}''::jsonb,''k'',''d'')',
    'select ops.j102_record_first_party_fact(''{}''::jsonb,''k'',''d'')',
    'select ops.rw02_record(''record-salesforce-write-readback'',''k'',''d'',''a'',''s'',''{}''::jsonb)',
    'select ops.register_execution_environment_provider(''{}''::jsonb, gen_random_uuid())'
  ] loop
    begin
      execute fn;
      raise exception 'f01 membership fixture: app_reader ran a writer: %', fn;
    exception when insufficient_privilege then
      null;
    end;
  end loop;
end $reader_cannot_write$;
reset session authorization;

-- 4. The patched reader DENY inside the F01 writers catches app_reader even if
-- EXECUTE were ever granted to the reader bundle.
grant execute on function ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text) to carr_reader;
grant execute on function ops.f01_register_derivative_link(jsonb,text,text) to carr_reader;
set session authorization app_reader;
do $reader_deny$
begin
  begin
    perform ops.f01_record_document('{}'::jsonb, '{}'::jsonb, '{}'::jsonb, 'k', 'd', 'x');
    raise exception 'f01 membership fixture: the reader deny did not fire in f01_record_document';
  exception when others then
    if sqlerrm not like '%f01_producer_principal_refused%' then raise; end if;
  end;
  begin
    perform ops.f01_register_derivative_link('{}'::jsonb, 'k', 'd');
    raise exception 'f01 membership fixture: the reader deny did not fire in f01_register_derivative_link';
  exception when others then
    if sqlerrm not like '%f01_producer_principal_refused%' then raise; end if;
  end;
end $reader_deny$;
reset session authorization;

-- 5. Refused, exactly as before 0732: logins whose membership reaches a bundle
-- but that are not a plain runtime login of it.
set session authorization f01t_exporter;
do $exporter$
begin
  perform ops.f01_read('current_policy', '{}'::jsonb);
  raise exception 'f01 membership fixture: an exporter login was admitted';
exception when others then
  if sqlerrm not like '%f01_principal_refused: f01t_exporter%' then raise; end if;
end $exporter$;
reset session authorization;

set session authorization f01t_owner_like;
select set_config('carr.acting_actor_slug', 'synthetic-f01-writer', true);
do $owner_like$
begin
  begin
    perform ops.f01_read('current_policy', '{}'::jsonb);
    raise exception 'f01 membership fixture: an owner-shaped login was admitted to read';
  exception when others then
    if sqlerrm not like '%f01_principal_refused: f01t_owner_like%' then raise; end if;
  end;
  begin
    perform ops.f01_record_document('{}'::jsonb, '{}'::jsonb, '{}'::jsonb, 'k', 'd', 'x');
    raise exception 'f01 membership fixture: an owner-shaped login was admitted to write';
  exception when others then
    if sqlerrm not like '%f01_principal_refused: f01t_owner_like%' then raise; end if;
  end;
end $owner_like$;
reset session authorization;

set session authorization f01t_jobs_writer;
select set_config('carr.acting_actor_slug', 'synthetic-f01-writer', true);
do $jobs_writer$
begin
  begin
    perform ops.f01_read('current_policy', '{}'::jsonb);
    raise exception 'f01 membership fixture: a jobs login holding carr_writer was admitted';
  exception when others then
    if sqlerrm not like '%f01_principal_refused: f01t_jobs_writer%' then raise; end if;
  end;
  begin
    perform ops.register_execution_environment_provider('{}'::jsonb, gen_random_uuid());
    raise exception 'f01 membership fixture: a jobs login registered a provider';
  exception when others then
    if sqlerrm not like '%requires the authority connection or a sponsored writer session%' then raise; end if;
  end;
  begin
    perform ops.transition_proposed_eval_candidate('WR-000000', 'f01t-none', 'accepted', '{}'::jsonb, gen_random_uuid());
    raise exception 'f01 membership fixture: a jobs login transitioned an eval candidate';
  exception when others then
    if sqlerrm not like '%requires the authority connection or a sponsored writer session%' then raise; end if;
  end;
end $jobs_writer$;
reset session authorization;

-- carr_jobs itself is a LOGIN holding a DIRECT EXECUTE on j102_read. It was
-- refused by the literal gate and must stay refused by the classifier.
set session authorization carr_jobs;
do $jobs_login$
begin
  perform ops.j102_read('first_party_record', '{}'::jsonb);
  raise exception 'f01 membership fixture: the carr_jobs login was admitted to j102_read';
exception when others then
  if sqlerrm not like '%f01_principal_refused: carr_jobs%' then raise; end if;
end $jobs_login$;
reset session authorization;

set session authorization neondb_owner;
select set_config('carr.acting_actor_slug', 'synthetic-f01-writer', true);
do $schema_owner$
begin
  perform ops.f01_read('current_policy', '{}'::jsonb);
  raise exception 'f01 membership fixture: the schema owner was admitted';
exception when others then
  if sqlerrm not like '%f01_principal_refused: neondb_owner%' then raise; end if;
end $schema_owner$;
reset session authorization;

set session authorization f01t_unrelated;
do $unrelated$
begin
  perform ops.f01_read('current_policy', '{}'::jsonb);
  raise exception 'f01 membership fixture: an unrelated login was admitted';
exception when insufficient_privilege then
  null;   -- no schema or function privilege at all, before and after 0732
end $unrelated$;
reset session authorization;

rollback;
