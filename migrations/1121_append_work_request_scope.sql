-- rollback: forward-only; appended criteria are scope the partners asked for, and the arm admits no other change.
-- expand-contract: expand with a scope-append receipt, its definer writer, and one receipt-backed guard arm.
-- lock-review: new empty table; replaces one trigger function body; no table rewrite or scan.
-- WR663.S1.J1: append-work-request-scope adds criteria after the recorded ones and may extend desired_outcome.
create table ops.work_request_scope_append_receipt (
  idempotency_key uuid primary key,
  work_request_id uuid not null references ops.work_request(id),
  base_version integer not null check (base_version >= 1),
  result_version integer not null,
  appended jsonb not null check (jsonb_typeof(appended) = 'array' and jsonb_array_length(appended) between 1 and 12),
  outcome_addendum text check (outcome_addendum is null or char_length(outcome_addendum) between 1 and 500),
  source_ref text not null check (btrim(source_ref) <> '' and char_length(source_ref) <= 300),
  appended_by_actor_id uuid not null references public.actor(id),
  appended_at timestamptz not null default now(),
  check (result_version = base_version + 1),
  unique (work_request_id, result_version)
);
revoke all on ops.work_request_scope_append_receipt from public, carr_reader, carr_writer, carr_authority, carr_jobs, dot_reader;
grant select on ops.work_request_scope_append_receipt to carr_reader;

create or replace function ops.append_work_request_scope(p_work_request text, p_base_version integer, p_criteria jsonb,
  p_outcome_addendum text, p_source_ref text, p_actor_slug text, p_idempotency_key uuid)
returns table(ref text, state text, version integer, criteria_count integer, replayed boolean)
language plpgsql security definer set search_path to 'pg_catalog', 'ops', 'pg_temp' as $$
declare
  v_actor public.actor%rowtype;
  w ops.work_request%rowtype;
  r ops.work_request_scope_append_receipt%rowtype;
  v_addendum text := nullif(btrim(coalesce(p_outcome_addendum, '')), '');
  v_appended jsonb;
  v_outcome text;
begin
  if p_idempotency_key is null or coalesce(btrim(p_work_request), '') !~ '^WR-[0-9]{1,12}$'
     or p_base_version is null or p_base_version < 1
     or coalesce(btrim(p_source_ref), '') = '' or char_length(p_source_ref) > 300
     or (p_outcome_addendum is not null and (v_addendum is null or char_length(v_addendum) > 500))
     or jsonb_typeof(p_criteria) is distinct from 'array' or jsonb_array_length(p_criteria) not between 1 and 12
     or exists (select 1 from jsonb_array_elements(p_criteria) x
                 where case when jsonb_typeof(x) <> 'object' then true
                            else (select array_agg(k order by k) from jsonb_object_keys(x) k) is distinct from array['id','text']
                              or jsonb_typeof(x->'id') is distinct from 'string' or jsonb_typeof(x->'text') is distinct from 'string'
                              or x->>'id' !~ '^[A-Z][A-Z0-9-]{1,63}$'
                              or btrim(x->>'text') = '' or char_length(btrim(x->>'text')) > 500 end) then
    raise exception 'invalid_scope_append: supply a Work Request, base version, 1-12 string criteria, source_ref and UUID key';
  end if;
  select a.* into v_actor from public.actor a where a.slug = p_actor_slug;
  if v_actor.id is null then raise exception 'unknown_actor: unknown actor %', p_actor_slug; end if;
  if not coalesce(v_actor.active and (v_actor.kind = 'human'
       or (v_actor.kind = 'automation' and nullif(btrim(current_setting('carr.sponsoring_human_slug', true)), '') in ('joe', 'dell'))), false) then
    raise exception 'sponsored_actor_required: appending scope requires an active human actor or a sponsored agent' using errcode = '42501';
  end if;
  select coalesce(jsonb_agg(jsonb_build_object('id', x.e->>'id', 'text', btrim(x.e->>'text')) order by x.o), '[]'::jsonb)
    into v_appended from jsonb_array_elements(p_criteria) with ordinality x(e, o);

  perform pg_advisory_xact_lock(hashtextextended('work-request-scope-append:' || p_idempotency_key, 0));
  select x.* into r from ops.work_request_scope_append_receipt x where x.idempotency_key = p_idempotency_key;
  if found then
    select x.* into w from ops.work_request x where x.id = r.work_request_id;
    if w.ref is distinct from p_work_request or r.base_version <> p_base_version or r.appended is distinct from v_appended
       or r.outcome_addendum is distinct from v_addendum or r.source_ref is distinct from p_source_ref
       or r.appended_by_actor_id is distinct from v_actor.id then
      raise exception 'scope_append_receipt_conflict: idempotency key already names a different scope append';
    end if;
    return query select w.ref, w.state, r.result_version, jsonb_array_length(w.acceptance_criteria), true;
    return;
  end if;

  select x.* into w from ops.work_request x where x.ref = p_work_request for update;
  if w.id is null then raise exception 'work_request_not_found: no Work Request %', p_work_request; end if;
  if w.state in ('confirmed_closed', 'declined', 'superseded', 'failed') then
    raise exception 'work_request_terminal_state: % is %', w.ref, w.state;
  end if;
  if w.version <> p_base_version then
    raise exception 'work_request_version_conflict: % is at version %, not %', w.ref, w.version, p_base_version;
  end if;
  if w.state not in ('captured', 'triaged') then
    raise exception 'work_request_scope_frozen: % is %', w.ref, w.state;
  end if;
  if (select count(distinct e->>'id') from jsonb_array_elements(w.acceptance_criteria || v_appended) e)
       <> jsonb_array_length(w.acceptance_criteria || v_appended) then
    raise exception 'duplicate_criterion_id: appended criterion ids must be new on %', w.ref;
  end if;
  if jsonb_array_length(w.acceptance_criteria || v_appended) > 12 then
    raise exception 'criteria_cap_exceeded: % would hold % criteria', w.ref, jsonb_array_length(w.acceptance_criteria || v_appended);
  end if;
  v_outcome := case when v_addendum is null then w.desired_outcome
                    else coalesce(nullif(w.desired_outcome, '') || chr(10) || chr(10), '') || v_addendum end;
  if char_length(coalesce(v_outcome, '')) > 4000 then
    raise exception 'invalid_scope_append: the desired outcome would pass 4000 characters';
  end if;

  insert into ops.work_request_scope_append_receipt
    (idempotency_key, work_request_id, base_version, result_version, appended, outcome_addendum, source_ref, appended_by_actor_id)
  values (p_idempotency_key, w.id, w.version, w.version + 1, v_appended, v_addendum, p_source_ref, v_actor.id);
  update ops.work_request x set acceptance_criteria = x.acceptance_criteria || v_appended, desired_outcome = v_outcome,
    version = x.version + 1, updated_at = now()
   where x.id = w.id returning x.* into w;
  return query select w.ref, w.state, w.version, jsonb_array_length(w.acceptance_criteria), false;
end $$;
revoke all on function ops.append_work_request_scope(text, integer, jsonb, text, text, text, uuid)
  from public, carr_reader, carr_authority, carr_jobs, dot_reader;
grant execute on function ops.append_work_request_scope(text, integer, jsonb, text, text, text, uuid) to carr_writer;

do $scope$
declare definition text; marker text := '  if old.state = ''captured''';
begin
  definition := pg_get_functiondef('ops.sourced_work_request_is_immutable()'::regprocedure);
  if position('-- scope append' in definition) = 0 then
    if position(marker in definition) = 0 then raise exception 'Sourced lifecycle insertion point drifted'; end if;
    definition := regexp_replace(definition, marker, $arm$  -- scope append: recorded criteria stay an exact prefix and only ops.append_work_request_scope writes the receipt.
  if old.state in ('captured','triaged') and new.state=old.state and new.version=old.version+1
     and (to_jsonb(new)-array['acceptance_criteria','desired_outcome','version','updated_at'])
       is not distinct from
       (to_jsonb(old)-array['acceptance_criteria','desired_outcome','version','updated_at'])
     and jsonb_typeof(new.acceptance_criteria)='array'
     and jsonb_array_length(new.acceptance_criteria) between jsonb_array_length(old.acceptance_criteria)+1 and 12
     and coalesce((select jsonb_agg(x.e order by x.o) from jsonb_array_elements(new.acceptance_criteria) with ordinality x(e,o)
                    where x.o<=jsonb_array_length(old.acceptance_criteria)),'[]'::jsonb)=old.acceptance_criteria
     and not exists (select 1 from jsonb_array_elements(new.acceptance_criteria) with ordinality x(e,o)
                      where x.o>jsonb_array_length(old.acceptance_criteria)
                        and case when jsonb_typeof(x.e)<>'object' then true
                                 else (select array_agg(k order by k) from jsonb_object_keys(x.e) k) is distinct from array['id','text']
                                   or jsonb_typeof(x.e->'id') is distinct from 'string'
                                   or jsonb_typeof(x.e->'text') is distinct from 'string'
                                   or x.e->>'id' !~ '^[A-Z][A-Z0-9-]{1,63}$'
                                   or btrim(x.e->>'text')='' or char_length(x.e->>'text')>500 end)
     and (select count(distinct e->>'id') from jsonb_array_elements(new.acceptance_criteria) e)=jsonb_array_length(new.acceptance_criteria)
     and coalesce(char_length(new.desired_outcome),0)<=4000
     and exists (
       select 1 from ops.work_request_scope_append_receipt r
        where r.work_request_id=old.id and r.base_version=old.version and r.result_version=new.version
          and r.appended=(select jsonb_agg(x.e order by x.o) from jsonb_array_elements(new.acceptance_criteria) with ordinality x(e,o)
                           where x.o>jsonb_array_length(old.acceptance_criteria))
          and case when r.outcome_addendum is null then new.desired_outcome is not distinct from old.desired_outcome
                   else new.desired_outcome=coalesce(nullif(old.desired_outcome,'')||chr(10)||chr(10),'')||r.outcome_addendum end
     ) then
    return new;
  end if;
$arm$ || marker);
    definition := replace(definition, 'or ready-to-declined transitions', 'or ready-to-declined transitions, or receipt-backed scope appends');
    execute definition;
  end if;
end $scope$;

-- Account for exactly the new definer entry point in the existing live catalog.
do $scope_grants$
declare contract jsonb := ops.scac_current_grant_contract(); entry jsonb;
begin
  select jsonb_build_object('config',to_jsonb(p.proconfig),'grantee','carr_writer',
    'parallel',p.proparallel::text,'grantable',false,'privilege','execute',
    'signature','ops.append_work_request_scope('||pg_get_function_identity_arguments(p.oid)||')',
    'volatility',p.provolatile::text,'ingress_key','db-function-acl:ops.append_work_request_scope('
      ||pg_get_function_identity_arguments(p.oid)||'):carr_writer:execute',
    'ingress_kind','db_function_acl','function_kind',p.prokind::text,'security_definer',p.prosecdef)
    into strict entry from pg_proc p where p.oid='ops.append_work_request_scope(text,integer,jsonb,text,text,text,uuid)'::regprocedure;
  contract := jsonb_set(contract,'{secdef_execute}',contract->'secdef_execute'||jsonb_build_array(entry));
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb '
    'language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $scope_grants$;
