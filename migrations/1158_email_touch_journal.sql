-- rollback: forward-only; retain frozen intent and observation custody.
-- expand-contract: additive bounded email intent journal.
-- lock-review: new records only; no business table rewrites.
set local lock_timeout='5s';
set local statement_timeout='30s';

create table ops.email_touch_expectation_journal (
 tenant text not null check(tenant='carr-internal'),
 digest text not null check(digest ~ '^[0-9a-f]{64}$'),
 capture_key uuid not null unique,
 actor_id uuid not null references public.actor(id),
 principal_slug text not null check(principal_slug in ('joe','dell')),
 producer_sha text not null check(producer_sha ~ '^[0-9a-f]{40}$'),
 mailbox_scope_hash text not null check(mailbox_scope_hash ~ '^[0-9a-f]{64}$'),
 input_digest text not null,
 raw bytea not null check(octet_length(raw) between 1 and 1048576),
 provenance jsonb not null,
 captured_at timestamptz not null default clock_timestamp(),
 primary key(tenant,digest),
 unique(tenant,mailbox_scope_hash,producer_sha),
 check(digest=encode(public.digest(raw,'sha256'),'hex'))
);
create index email_touch_journal_first on ops.email_touch_expectation_journal(principal_slug,producer_sha,mailbox_scope_hash,captured_at,capture_key);
create table ops.email_touch_journal_outcome (
 tenant text not null,
 digest text not null,
 observation_id uuid not null,
 original_key uuid not null,
 observation jsonb not null,
 recorded_at timestamptz not null default clock_timestamp(),
 primary key(tenant,digest,observation_id),
 foreign key(tenant,digest) references ops.email_touch_expectation_journal(tenant,digest)
);
revoke all on ops.email_touch_expectation_journal,ops.email_touch_journal_outcome from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.email_touch_journal_immutable() returns trigger
language plpgsql set search_path=pg_catalog,pg_temp as $$
begin raise exception 'email_journal_immutable'; end $$;
create trigger email_journal_immutable before update or delete or truncate on ops.email_touch_expectation_journal
 for each statement execute function ops.email_touch_journal_immutable();
create trigger email_outcome_immutable before update or delete or truncate on ops.email_touch_journal_outcome
 for each statement execute function ops.email_touch_journal_immutable();

create function ops.email_touch_journal_custody() returns trigger
language plpgsql set search_path=pg_catalog,pg_temp as $$
declare j jsonb:=convert_from(new.raw,'UTF8')::jsonb;
begin
 if j->>'tenant' is distinct from new.tenant or j->>'actor_id' is distinct from new.actor_id::text
  or j->>'principal_slug' is distinct from new.principal_slug or j->>'release_sha' is distinct from new.producer_sha
  or new.provenance->>'producer_sha' is distinct from new.producer_sha
  or new.provenance->>'mailbox_scope_hash' is distinct from new.mailbox_scope_hash
  or new.provenance->>'start' is distinct from j->>'start' or new.provenance->>'end' is distinct from j->>'end'
  or new.provenance->'selected_keys' is distinct from (select jsonb_agg(e->'original_key') from jsonb_array_elements(j->'expected') e) then
  raise exception 'email_journal_custody_mismatch';
 end if;
 return new;
end $$;
create trigger email_journal_custody before insert on ops.email_touch_expectation_journal
 for each row execute function ops.email_touch_journal_custody();

create function ops.email_touch_journal_principal() returns text
language plpgsql stable set search_path=pg_catalog,pg_temp as $$
declare a text; p text;
begin
 select slug into a from public.actor where id=ops.portfolio_writer_actor_id() and active;
 p:=case when a in ('joe','dell') then a when a in ('joe-local','dell-local') then current_setting('carr.sponsoring_human_slug',true) end;
 if p is null or p not in ('joe','dell') or (a like '%-local' and a<>p||'-local')
  or current_setting('carr.organization_tenant_id',true) is distinct from 'carr-internal' then
  raise exception 'email_journal_identity_refused';
 end if;
 return p;
end $$;

create function ops.capture_email_touch_journal(p_input jsonb,p_raw bytea) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid:=ops.portfolio_writer_actor_id(); p text:=ops.email_touch_journal_principal();
 k uuid:=(p_input->>'idempotency_key')::uuid; h text; j jsonb; d text;
 prior ops.email_touch_expectation_journal%rowtype; n integer; slug text;
begin
 if (select count(*) from jsonb_object_keys(p_input))<>10
  or exists(select 1 from jsonb_each(p_input) e where e.value='null'::jsonb)
  or jsonb_typeof(p_input->'proposals') is distinct from 'array'
  or not p_input ?& array['idempotency_key','principal_slug','producer_sha','mailbox_scope_hash','execution_id','source_id','source_version','projection_digest','message_hash','proposals']
  or p_input->>'producer_sha' !~ '^[0-9a-f]{40}$'
  or p_input->>'mailbox_scope_hash' !~ '^[0-9a-f]{64}$'
  or p_input->>'message_hash' !~ '^[0-9a-f]{64}$'
  or p_input->>'projection_digest' !~ '^[0-9a-f]{64}$'
  or p_input->>'source_id' !~ '^outlook-intake:v1:[0-9a-f]{64}$'
  or p_input->>'source_version' is distinct from 'sha256:'||(p_input->>'projection_digest') then
  raise exception 'email_journal_input_invalid';
 end if;
 perform (p_input->>'execution_id')::uuid;
 if p_input->>'principal_slug' is distinct from p then raise exception 'email_journal_principal_mismatch'; end if;
 h:=encode(public.digest(p_input::text,'sha256'),'hex');
 perform pg_advisory_xact_lock(hashtextextended(k::text,0));
 select * into prior from ops.email_touch_expectation_journal where capture_key=k;
 if found then
  if prior.input_digest<>h or prior.actor_id<>a then raise exception 'email_journal_capture_conflict'; end if;
 else
  if p_raw is null then return null; end if;
  if octet_length(p_raw) not between 1 and 1048576 then raise exception 'email_journal_size'; end if;
  j:=convert_from(p_raw,'UTF8')::jsonb;
  select actor.slug into slug from public.actor where id=a;
  n:=jsonb_array_length(p_input->'proposals');
  if exists(select 1 from jsonb_array_elements(p_input->'proposals') q where
    (select count(*) from jsonb_object_keys(q))<>4
    or (q->>'original_key')::uuid::text is distinct from q->>'original_key'
    or q->>'occurred_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$') then
    raise exception 'email_journal_proposal_invalid';
  end if;
  if n not between 1 and 500 or (select count(*) from jsonb_object_keys(j))<>10
    or j->>'schema' is distinct from 'email-touch-expectations.v1'
    or j->>'tenant' is distinct from 'carr-internal' or j->>'principal_slug' is distinct from p
    or j->>'actor_id' is distinct from a::text or j->>'actor_slug' is distinct from slug
    or j->>'release_sha' is distinct from p_input->>'producer_sha'
    or (j->>'expected_count')::integer is distinct from n or jsonb_array_length(j->'expected')<>n
    or j->>'start' is distinct from p_input#>>'{proposals,0,occurred_at}'
    or (j->>'end')::timestamptz<>(j->>'start')::timestamptz+interval '1 millisecond'
    or (select count(distinct x->>'original_key') from jsonb_array_elements(j->'expected') x)<>n
    or (select count(distinct (x->>'target_type',x->>'target_id')) from jsonb_array_elements(j->'expected') x)<>n then
    raise exception 'email_journal_shape';
  end if;
  if exists(select 1 from jsonb_array_elements(j->'expected') e where
   (select count(*) from jsonb_object_keys(e))<>7
   or e->>'source_id' is distinct from p_input->>'source_id'
   or e->>'source_version' is distinct from p_input->>'source_version'
   or not exists(select 1 from jsonb_array_elements(p_input->'proposals') q
    join public.v_ref_index r on (r.ref=q->>'ref' or r.subject_id::text=q->>'ref')
    join public.party party on party.id=r.party_id and party.deleted_at is null
    where q->>'original_key'=e->>'original_key' and q->>'occurred_at'=e->>'occurred_at'
     and q->>'occurred_at'=j->>'start' and q->>'kind' in ('email_in','email_out') and not r.merged
     and r.subject_type=e->>'target_type' and r.subject_id::text=e->>'target_id'
     and e->>'role'=case r.subject_type when 'client' then 'client' when 'lead' then 'prospect' when 'vendor' then 'vendor' end)) then
    raise exception 'email_journal_target_mismatch';
  end if;
  if exists(select 1 from public.event e join jsonb_array_elements(j->'expected') x
   on e.idempotency_key=x->>'original_key' where e.actor_id=a and e.verb='log-activity') then
   raise exception 'email_journal_already_attempted';
  end if;
  d:=encode(public.digest(p_raw,'sha256'),'hex');
  insert into ops.email_touch_expectation_journal(tenant,digest,capture_key,actor_id,principal_slug,producer_sha,mailbox_scope_hash,input_digest,raw,provenance)
   values('carr-internal',d,k,a,p,p_input->>'producer_sha',p_input->>'mailbox_scope_hash',h,p_raw,
    (p_input-'proposals')||jsonb_build_object('projection_schema','outlook-source-projection.v1','selected_keys',
     (select jsonb_agg(x->'original_key') from jsonb_array_elements(j->'expected') x),'start',j->'start','end',j->'end'));
  select * into prior from ops.email_touch_expectation_journal where capture_key=k;
 end if;
 return jsonb_build_object('ok',true,'digest',prior.digest,'reference','sha256:'||prior.digest,
   'raw_base64',replace(encode(prior.raw,'base64'),E'\n',''),'actor_id',prior.actor_id,
   'actor_slug',convert_from(prior.raw,'UTF8')::jsonb->>'actor_slug','captured_at',prior.captured_at);
end $$;

create function ops.record_email_touch_journal_outcome(p_reference text,p_observations jsonb,p_release text) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid:=ops.portfolio_writer_actor_id(); p text:=ops.email_touch_journal_principal();
 j ops.email_touch_expectation_journal%rowtype; o jsonb; prior jsonb; d text:=substring(p_reference from 8);
begin
 if p_reference !~ '^sha256:[0-9a-f]{64}$' or jsonb_array_length(p_observations) not between 1 and 500 then raise exception 'email_journal_outcome_invalid'; end if;
 select * into j from ops.email_touch_expectation_journal where tenant='carr-internal' and digest=d and actor_id=a and principal_slug=p;
 if not found then raise exception 'email_journal_not_found'; end if;
 if j.producer_sha is distinct from p_release then raise exception 'email_journal_release_mismatch'; end if;
 perform pg_advisory_xact_lock(hashtextextended(d,0));
 for o in select value from jsonb_array_elements(p_observations) loop
  if (select count(*) from jsonb_object_keys(o))<>5
   or not o ?& array['observation_id','original_key','status','activity_id','error_code']
   or o->>'status' is null or o->>'status' not in ('acknowledged','uncertain','refused')
   or (o->>'error_code' is not null and o->>'error_code' !~ '^[a-z][a-z0-9_]{0,63}$')
   or not exists(select 1 from jsonb_array_elements(convert_from(j.raw,'UTF8')::jsonb->'expected') e where e->>'original_key'=o->>'original_key') then raise exception 'email_journal_outcome_invalid'; end if;
  perform (o->>'activity_id')::uuid;
  select observation into prior from ops.email_touch_journal_outcome where tenant=j.tenant and digest=d and observation_id=(o->>'observation_id')::uuid;
  if found then
   if prior<>o then raise exception 'email_journal_observation_conflict'; end if;
  else
   insert into ops.email_touch_journal_outcome(tenant,digest,observation_id,original_key,observation)
    values(j.tenant,d,(o->>'observation_id')::uuid,(o->>'original_key')::uuid,o);
  end if;
 end loop;
 return jsonb_build_object('ok',true);
end $$;

revoke all on function ops.email_touch_journal_immutable(),ops.email_touch_journal_custody(),ops.email_touch_journal_principal(),
 ops.capture_email_touch_journal(jsonb,bytea),ops.record_email_touch_journal_outcome(text,jsonb,text)
 from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;
grant execute on function ops.capture_email_touch_journal(jsonb,bytea),ops.record_email_touch_journal_outcome(text,jsonb,text) to carr_writer,carr_authority;

do $append_grant$
declare contract jsonb; entries jsonb;
begin
 contract:=ops.scac_current_grant_contract();
 select jsonb_agg(jsonb_build_object(
  'grantee',grantee,'signature',signature,'grantable',false,'privilege','execute',
  'ingress_key','db-function-acl:'||signature||':'||grantee||':execute',
  'ingress_kind','db_function_acl','function_kind','f','security_definer',true,
  'volatility','v','parallel','u','config',jsonb_build_array('search_path=pg_catalog, pg_temp')))
 into entries from (values
  ('ops.capture_email_touch_journal(p_input jsonb, p_raw bytea)'),
  ('ops.record_email_touch_journal_outcome(p_reference text, p_observations jsonb, p_release text)')
 ) functions(signature) cross join (values('carr_writer'),('carr_authority')) roles(grantee);
 contract:=jsonb_set(contract,'{secdef_execute}',(contract->'secdef_execute')||entries);
 execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L',
  'select '||quote_literal(contract::text)||'::jsonb');
end $append_grant$;
