-- rollback: forward-only; captured requests and receipt provenance must be retained.
-- lock-review: replacing the work_request CHECK takes ACCESS EXCLUSIVE; bounded by lock_timeout.
set local lock_timeout='5s';
set local statement_timeout='60s';
create table ops.future_work_link (
  organization_tenant_id text not null check (organization_tenant_id='carr-internal'),
  source_ref text not null,
  work_request_id uuid references ops.work_request(id),
  source_verb text not null,
  source_key text not null,
  created_at timestamptz not null default now(),
  primary key(organization_tenant_id,source_ref)
);
create index future_work_link_work_request on ops.future_work_link(work_request_id);
grant select on ops.future_work_link to carr_reader,carr_writer;
grant insert on ops.future_work_link to carr_writer;
grant update on ops.future_work_link to carr_writer;
-- Extend the existing provenance alternatives without restating their lifecycle rules.
do $record_capture_shape$
declare existing_shape text;
begin
  select pg_get_expr(conbin,conrelid) into strict existing_shape from pg_constraint
    where conrelid='ops.work_request'::regclass and conname='work_request_sourced_capture_shape';
  alter table ops.work_request drop constraint work_request_sourced_capture_shape;
  execute format('alter table ops.work_request add constraint work_request_sourced_capture_shape check ((%s) or '
    '(capture_idempotency_key is not null and organization_tenant_id=''carr-internal'' '
    'and doctrine_section_id is null and doctrine_revision_id is null and sourced_capture_sequence is not null '
    'and program_key is null and program_ordinal is null and origin_ref ~ ''^record:[a-z_]+:.+$''))', existing_shape);
end $record_capture_shape$;

CREATE OR REPLACE FUNCTION ops.capture_sourced_work_request(p_origin_ref text, p_title text, p_desired_outcome text, p_acceptance_criteria jsonb, p_doctrine_section_id uuid, p_doctrine_revision_id uuid, p_idempotency_key uuid) RETURNS TABLE(id uuid, ref text, state text, version integer, organization_tenant_id text, doctrine_section_id uuid, doctrine_revision_id uuid, doctrine_source_label text, source_current boolean, replayed boolean)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'ops', 'pg_temp'
    AS $_$
declare
  v_section public.doctrine_section%rowtype;
  v_document public.doctrine_document%rowtype;
  v_existing ops.work_request%rowtype;
  v_sequence bigint;
  v_ref text;
  v_label text;
begin
  if p_idempotency_key is null
     or (coalesce(btrim(p_origin_ref), '') !~ '^doctrine:[a-z0-9][a-z0-9-]*#[a-z0-9][a-z0-9-]*$' and p_origin_ref !~ '^record:[a-z_]+:.+$')
     or (p_origin_ref not like 'record:%' and char_length(p_origin_ref) > 300)
     or coalesce(btrim(p_title), '') = '' or char_length(p_title) > 240
     or coalesce(btrim(p_desired_outcome), '') = '' or (p_origin_ref not like 'record:%' and char_length(p_desired_outcome) > 4000)
     or jsonb_typeof(p_acceptance_criteria) <> 'array'
     or jsonb_array_length(p_acceptance_criteria) not between 1 and 12
     or exists (select 1 from jsonb_array_elements(p_acceptance_criteria) x
                 where jsonb_typeof(x) <> 'object' or x ?| array['id','text'] is false
                    or (select array_agg(k order by k) from jsonb_object_keys(x) k) <> array['id','text']
                    or coalesce(x->>'id','') !~ '^[A-Z][A-Z0-9-]{1,63}$'
                    or coalesce(btrim(x->>'text'),'') = '' or char_length(x->>'text') > 500) then
    raise exception 'sourced capture requires bounded exact acceptance criteria plus a doctrine origin, title, outcome, and UUID idempotency key';
  end if;
  if exists (select x->>'id' from jsonb_array_elements(p_acceptance_criteria) x
               group by x->>'id' having count(*) > 1) then
    raise exception 'sourced capture acceptance criterion IDs must be unique';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('program6-sourced-capture:' || p_idempotency_key, 0));
  select w.* into v_existing from ops.work_request w
   where w.capture_idempotency_key=p_idempotency_key;
  if found then
    if v_existing.organization_tenant_id is distinct from 'carr-internal'
       or v_existing.origin_ref is distinct from p_origin_ref
       or v_existing.title is distinct from p_title
       or v_existing.desired_outcome is distinct from p_desired_outcome
       or v_existing.acceptance_criteria is distinct from p_acceptance_criteria
       or v_existing.doctrine_section_id is distinct from p_doctrine_section_id
       or v_existing.doctrine_revision_id is distinct from p_doctrine_revision_id then
      raise exception 'idempotency key already names a different sourced capture';
    end if;
    select coalesce(s.title, s.section_key) into v_label from public.doctrine_section s where s.id=v_existing.doctrine_section_id;
    return query select v_existing.id, v_existing.ref, v_existing.state, v_existing.version,
      v_existing.organization_tenant_id, v_existing.doctrine_section_id,
      v_existing.doctrine_revision_id, v_label,
      exists (select 1 from public.doctrine_section s where s.id=v_existing.doctrine_section_id
                and s.status='active' and s.current_revision_id=v_existing.doctrine_revision_id), true;
    return;
  end if;

  if p_origin_ref like 'record:%' then
    if p_doctrine_section_id is not null or p_doctrine_revision_id is not null
       or not exists (select 1 from ops.future_work_link f join public.tool_call t on t.idempotency_key=f.source_key
         where 'record:'||f.source_ref=p_origin_ref and f.work_request_id is null
           and f.organization_tenant_id='carr-internal' and t.organization_tenant_id=f.organization_tenant_id
           and t.verb=f.source_verb) then
      raise exception 'record capture requires an exact committed-in-transaction tool receipt';
    end if;
  else
  select s.* into v_section from public.doctrine_section s
   where s.id=p_doctrine_section_id
   for update;
  if not found then
    raise exception 'sourced capture requires an exact current active shared doctrine revision';
  end if;
  select d.* into v_document from public.doctrine_document d where d.id=v_section.document_id for share;
  perform 1 from public.doctrine_revision r
   where r.id=p_doctrine_revision_id and r.section_id=p_doctrine_section_id for share;
  if not found or v_section.current_revision_id is distinct from p_doctrine_revision_id
     or v_section.status <> 'active'
     or v_document.visibility <> 'shared' then
    raise exception 'sourced capture requires an exact current active shared doctrine revision';
  end if;
  if p_origin_ref is distinct from ('doctrine:' || v_document.slug || '#' || v_section.section_key) then
    raise exception 'sourced capture origin reference does not name its exact doctrine evidence';
  end if;
  if not exists (select 1 from public.doctrine_revision r where r.id=p_doctrine_revision_id and r.section_id=p_doctrine_section_id) then
    raise exception 'sourced capture doctrine evidence is invented';
  end if;

  end if;

  select nextval('ops.work_request_ref_seq') into v_sequence;
  v_ref := 'WR-' || lpad(v_sequence::text, 6, '0');
  v_label := coalesce(v_section.title, v_section.section_key);
  insert into ops.work_request
    (ref,state,title,desired_outcome,acceptance_criteria,origin_ref,requester_actor,
     organization_tenant_id,doctrine_section_id,doctrine_revision_id,
     capture_idempotency_key,sourced_capture_sequence)
  values
    (v_ref,'captured',p_title,p_desired_outcome,p_acceptance_criteria,p_origin_ref,'mcp-authenticated',
     'carr-internal',p_doctrine_section_id,p_doctrine_revision_id,p_idempotency_key,v_sequence)
  returning * into v_existing;
  return query select v_existing.id, v_existing.ref, v_existing.state, v_existing.version,
    v_existing.organization_tenant_id, v_existing.doctrine_section_id,
    v_existing.doctrine_revision_id, v_label, true, false;
end;
$_$;


-- Account for the exact new writer grants in the existing live catalog.
do $future_work_grants$
declare contract jsonb := ops.scac_current_grant_contract(); privilege text;
begin
  foreach privilege in array array['insert','update'] loop
    contract := jsonb_set(contract,'{relation_dml}',contract->'relation_dml' || jsonb_build_array(jsonb_build_object(
      'ingress_key','db-relation-acl:ops.future_work_link:carr_writer:'||privilege,
      'ingress_kind','db_relation_acl','relation','ops.future_work_link','relation_kind','r',
      'grantee','carr_writer','privilege',privilege,'grantable',false)));
  end loop;
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb '
    'language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $future_work_grants$;
