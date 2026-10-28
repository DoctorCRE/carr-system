-- rollback: remove attach-memory and the gate impl first, then drop public.memory_session and delete the memory_attach_required gate row, the team-memory review policy and the team-memory and dreams-log documents (with their sections).
-- lock-review: new empty table; one row each into doctrine_gate_check and doctrine_review_policy and two into doctrine_document, all small registry tables.
-- WR-000562 slice S2 (docs/memory/team-memory-design.md §5, D2, D4, D7).

-- One row per session that attached to memory (D7). Rows are never updated:
-- the writer role gets INSERT only, so an attach receipt cannot be rewritten.
create table public.memory_session (
  attach_id uuid primary key default gen_random_uuid(),
  -- 'app' is the seam for S4: the governance-queue approval attaches as itself
  -- to retire an adopted proposal past memory_attach_required.
  surface text not null check (surface in ('claude_code','codex','routine','dot','dream','app')),
  native_session_id text check (length(btrim(native_session_id)) between 1 and 200),
  transcript_ref text check (length(btrim(transcript_ref)) between 1 and 1000),
  host text check (length(btrim(host)) between 1 and 200),
  actor_id uuid not null references public.actor(id),
  access text not null check (access in ('read_only','read_write')),
  attached_generation bigint not null check (attached_generation >= 0),
  attached_index_digest text not null check (attached_index_digest ~ '^[0-9a-f]{64}$'),
  attached_at timestamptz not null default now()
);
comment on table public.memory_session is
  'One attach-memory call (WR-000562 D7): what a session read (generation + index digest) and its access. Writes link back through doctrine_revision.session_key = attach_id.';

grant select on public.memory_session to carr_reader;
grant select, insert on public.memory_session to carr_writer;
-- attach-memory is a write verb (writer pool) that returns the rule-boot digest,
-- which reads this view; carr_writer is not a member of carr_reader.
grant select on public.v_compiled_rules to carr_writer;

-- D2: team memory is a distillation document with a 30-day staleness clock.
insert into public.doctrine_review_policy (name, max_age_days) values ('team-memory', 30);

insert into public.doctrine_document (slug, title, content_class, visibility, review_policy_id)
select 'team-memory', 'Team memory', 'distillation', 'shared', id
  from public.doctrine_review_policy where name = 'team-memory';
insert into public.doctrine_document (slug, title, content_class, visibility)
values ('dreams-log', 'Dreams log', 'distillation', 'shared');

-- D4: a gate is a function (gates.memory_attach_required in doctrine.js) and a row.
insert into public.doctrine_gate_check (check_key, description, severity, applies_to, impl_key, config)
values ('memory_attach_required',
        'Team memory writes and retires need a known attach_id from attach-memory with read_write access.',
        'block', '{"ops":["write","retire"],"content_classes":["distillation"]}',
        'gates.memory_attach_required', '{"slugs":["team-memory"]}');

-- Register the one new writer grant; preserve all existing grants and retirements.
do $contract$
declare contract jsonb := ops.scac_current_grant_contract();
begin
  contract := jsonb_set(contract, '{relation_dml}', (contract->'relation_dml') || jsonb_build_array(
    jsonb_build_object('grantee','carr_writer','relation','public.memory_session','grantable',false,
      'privilege','insert','ingress_key','db-relation-acl:public.memory_session:carr_writer:insert',
      'ingress_kind','db_relation_acl','relation_kind','r')));
  execute format(
    'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(contract::text) || '::jsonb');
end $contract$;
