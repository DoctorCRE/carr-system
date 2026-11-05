-- rollback: retire the v1 verbs before dropping their retained custody table and function.
-- expand-contract: additive provider-neutral checkpoint authority; no legacy continuity changes.
-- lock-review: new table only, one session lock, bounded row and replay history.
set local lock_timeout='5s';
set local statement_timeout='30s';
create table ops.session_checkpoint_v1_record (
 sponsor text not null check(sponsor in ('joe','dell')),
 authority_session uuid not null,
 binding jsonb not null,
 generation integer not null default 0 check(generation>=0),
 archived boolean not null default false,
 envelope jsonb,
 last_write_key text,
 receipts jsonb not null default '[]',
 updated_at timestamptz not null default now(),
 primary key(sponsor,authority_session),
 check(octet_length(binding::text)<=20000),
 check(octet_length(envelope::text)<=100000),
 check(jsonb_array_length(receipts)<=8)
);
revoke all on ops.session_checkpoint_v1_record from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;
create table ops.session_checkpoint_v1_key (
 sponsor text not null, authority_session uuid not null, key text not null,
 request_hash text not null,
 primary key(sponsor,authority_session,key)
);
revoke all on ops.session_checkpoint_v1_key from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;
create function ops.session_checkpoint_v1(p_tenant text,p_sponsor text,p_session uuid,p_operation text,p_args jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare r ops.session_checkpoint_v1_record; prior jsonb; answer jsonb; e jsonb:=p_args->'envelope'; old_hash text;
 request_hash text:=encode(sha256(convert_to(p_operation||p_args::text,'UTF8')),'hex');
begin
 if p_tenant is distinct from 'carr-internal' or p_sponsor not in ('joe','dell') or p_session is null
  or current_setting('carr.acting_actor_slug',true) is distinct from p_sponsor||'-local' then
  raise exception using errcode='42501',message='checkpoint_identity_required';
 end if;
 if p_operation not in ('read','write','archive') or jsonb_typeof(p_args->'binding') is distinct from 'object'
   or octet_length(p_args::text)>100000 then
  return jsonb_build_object('ok',false,'error','invalid_session_checkpoint');
 end if;
 if p_operation='read' then
  select * into r from ops.session_checkpoint_v1_record where sponsor=p_sponsor and authority_session=p_session;
  if not found then return jsonb_build_object('ok',true,'schema','session-checkpoint.v1','record',null); end if;
 else
  insert into ops.session_checkpoint_v1_record(sponsor,authority_session,binding)
   values(p_sponsor,p_session,p_args->'binding') on conflict do nothing;
  select * into r from ops.session_checkpoint_v1_record where sponsor=p_sponsor and authority_session=p_session for update;
 end if;
 if r.binding is distinct from p_args->'binding' then
  return jsonb_build_object('ok',false,'error','immutable_checkpoint_binding');
 end if;
 if p_operation<>'read' then
  if p_args->>'idempotency_key' is null then return jsonb_build_object('ok',false,'error','checkpoint_key_required'); end if;
  select item into prior from jsonb_array_elements(r.receipts) item where item->>'key'=p_args->>'idempotency_key';
  if prior is not null then
   if prior->'args' is distinct from p_args or prior->>'operation'<>p_operation then
    return jsonb_build_object('ok',false,'error','changed_checkpoint_replay');
   end if;
   return prior->'result';
  end if;
  select k.request_hash into old_hash from ops.session_checkpoint_v1_key k
   where sponsor=p_sponsor and authority_session=p_session and key=p_args->>'idempotency_key';
  if old_hash is not null then
   return jsonb_build_object('ok',false,'error',case when old_hash=request_hash then 'checkpoint_replay_expired' else 'changed_checkpoint_replay' end);
  end if;
  if r.archived then return jsonb_build_object('ok',false,'error','checkpoint_archived'); end if;
  if r.generation is distinct from (case when p_operation='write' then e->>'expected_generation' else p_args->>'expected_generation' end)::integer then
   return jsonb_build_object('ok',false,'error','checkpoint_cas_conflict');
  end if;
  if p_operation='write' then
   if e->'binding' is distinct from r.binding or jsonb_typeof(e->'checkpoint') is distinct from 'object'
     or octet_length((e->'checkpoint')::text)>40000 then
    return jsonb_build_object('ok',false,'error','invalid_session_checkpoint');
   end if;
   if r.envelope is not null and ((e->>'epoch')::integer<(r.envelope->>'epoch')::integer
      or (e->>'cursor')::integer<=(r.envelope->>'cursor')::integer) then
    return jsonb_build_object('ok',false,'error','old_checkpoint_event');
   end if;
   r.envelope:=e;
  else r.archived:=true;
  end if;
  r.generation:=r.generation+1;
  r.last_write_key:=p_args->>'idempotency_key';
 end if;
 answer:=jsonb_build_object('ok',true,'schema','session-checkpoint.v1','record',jsonb_build_object(
  'sponsor',p_sponsor,'authority_session',p_session,'binding',r.binding,'generation',r.generation,
  'archived',r.archived,'envelope',r.envelope,'last_write_key',r.last_write_key));
 if p_operation<>'read' then
  r.receipts:=r.receipts||jsonb_build_array(jsonb_build_object('key',p_args->>'idempotency_key','args',p_args,'operation',p_operation,'result',answer));
  select coalesce(jsonb_agg(item order by n),'[]') into r.receipts from jsonb_array_elements(r.receipts) with ordinality a(item,n)
   where n>jsonb_array_length(r.receipts)-8;
  insert into ops.session_checkpoint_v1_key values(p_sponsor,p_session,r.last_write_key,request_hash);
  update ops.session_checkpoint_v1_record set generation=r.generation,archived=r.archived,envelope=r.envelope,receipts=r.receipts,last_write_key=r.last_write_key,updated_at=now()
   where sponsor=p_sponsor and authority_session=p_session;
 end if;
 return answer;
end $$;
revoke all on function ops.session_checkpoint_v1(text,text,uuid,text,jsonb) from public;
grant execute on function ops.session_checkpoint_v1(text,text,uuid,text,jsonb) to carr_writer;

do $append_grant$
declare contract jsonb;
begin
 contract:=ops.scac_current_grant_contract();
 contract:=jsonb_set(contract,'{secdef_execute}',coalesce(contract->'secdef_execute','[]'::jsonb)||
  '[{"grantee":"carr_writer","signature":"ops.session_checkpoint_v1(p_tenant text, p_sponsor text, p_session uuid, p_operation text, p_args jsonb)","grantable":false,"privilege":"execute","ingress_key":"db-function-acl:ops.session_checkpoint_v1(p_tenant text, p_sponsor text, p_session uuid, p_operation text, p_args jsonb):carr_writer:execute","ingress_kind":"db_function_acl","function_kind":"f","security_definer":true,"volatility":"v","parallel":"u","config":["search_path=pg_catalog, pg_temp"]}]'::jsonb);
 execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $append_grant$;
