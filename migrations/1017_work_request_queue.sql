-- rollback: forward fix only; preserve minted slice and job identities.
create table ops.work_request_slice (
  work_request_id uuid not null references ops.work_request(id),
  ordinal integer not null check (ordinal > 0),
  title text not null check (length(btrim(title)) between 1 and 200),
  primary key (work_request_id, ordinal)
);
create table ops.work_request_job (
  work_request_id uuid not null,
  slice_ordinal integer not null,
  j_number integer not null check (j_number > 0),
  priority integer not null check (priority > 0),
  executor text not null check (length(btrim(executor)) between 1 and 100),
  model text not null check (length(btrim(model)) between 1 and 100),
  effort text not null check (length(btrim(effort)) between 1 and 100),
  card_title text not null check (length(card_title) <= 80 and card_title ~ '^[A-Za-z'']+( [A-Za-z'']+){1,2}$'),
  description text not null check (length(btrim(description)) between 1 and 500),
  created_at timestamptz not null default now(),
  primary key (work_request_id, slice_ordinal, j_number),
  foreign key (work_request_id, slice_ordinal) references ops.work_request_slice(work_request_id, ordinal)
);
create index work_request_job_priority on ops.work_request_job(priority, work_request_id, slice_ordinal, j_number);
grant select on ops.work_request_slice, ops.work_request_job to carr_reader, carr_writer;
grant insert on ops.work_request_slice, ops.work_request_job to carr_writer;
grant update on ops.work_request_job to carr_writer;
do $queue_grants$
declare contract jsonb := ops.scac_current_grant_contract(); relation_name text; privilege_name text;
begin
  for relation_name, privilege_name in values
    ('ops.work_request_slice','insert'), ('ops.work_request_job','insert'), ('ops.work_request_job','update') loop
    contract := jsonb_set(contract, '{relation_dml}', contract->'relation_dml' || jsonb_build_array(jsonb_build_object(
      'ingress_key','db-relation-acl:'||relation_name||':carr_writer:'||privilege_name,
      'ingress_kind','db_relation_acl','relation',relation_name,'relation_kind','r',
      'grantee','carr_writer','privilege',privilege_name,'grantable',false)));
  end loop;
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb '
    'language sql immutable set search_path=pg_catalog as %L', 'select '||quote_literal(contract::text)||'::jsonb');
end $queue_grants$;
