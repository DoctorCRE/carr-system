-- 0345_governance_queue_projection.sql
-- WR-000019 slice S6 (Obedience & Autonomy heavy build), BATCH REVIEW QUEUE.
--
-- A single read-only projection across every pending governance decision, so
-- Joe reviews in bulk on his own schedule instead of one verb at a time:
--   * pending rule approvals    -- rule.status='proposed' with an admitted
--     ops.rule_admission row (normalized, enforcement-checked, waiting only on
--     approve-rule)
--   * pending guidance import batches -- ops.guidance_import_batch rows with
--     no ops.guidance_import_decision_event yet (staged, waiting on
--     decide-guidance-import-batch)
--   * pending retrieval proposals -- retrieval_proposal rows with
--     status='pending' (waiting on approve-retrieval-proposals)
--
-- carr_reader (the role the read-verb credential connects as) has no grant on
-- public.rule or public.retrieval_proposal -- both are carr_writer/carr_authority
-- only -- so the projection has to run as a SECURITY DEFINER function, the same
-- reason read-execution-environment-providers exists as a function rather than
-- a direct multi-table read. This function is read-only: it selects, it never
-- inserts, updates, or deletes, and it opens no new write path.

begin;

create function ops.read_governance_queue() returns jsonb
    language sql stable security definer
    set search_path to 'ops', 'public', 'pg_temp'
    as $$
  select jsonb_build_object(
    'pending_rule_approvals', coalesce((
      select jsonb_agg(jsonb_build_object(
        'rule_id', r.id, 'statement', r.statement, 'human_quote', r.human_quote,
        'scope', r.scope, 'taught_at', r.created_at,
        'enforcement_class', ra.enforcement_class, 'binding_moment', ra.binding_moment,
        'admission_reason', ra.reason, 'enforcement_status', ra.enforcement_status,
        'fixture_refs', ra.fixture_refs, 'admitted_at', ra.admitted_at
      ) order by ra.admitted_at asc, r.id)
      from rule r join ops.rule_admission ra on ra.rule_id = r.id
      where r.status = 'proposed' and ra.state = 'admitted'
    ), '[]'::jsonb),
    'pending_guidance_import_batches', coalesce((
      select jsonb_agg(jsonb_build_object(
        'batch_id', b.id, 'manifest_digest', b.manifest_digest, 'reason', b.reason,
        'staging_key', b.staging_key, 'staged_at', b.created_at,
        'entry_count', (select count(*) from ops.guidance_import_entry e where e.batch_id = b.id)
      ) order by b.created_at asc, b.id)
      from ops.guidance_import_batch b
      where not exists (select 1 from ops.guidance_import_decision_event d where d.batch_id = b.id)
    ), '[]'::jsonb),
    'pending_retrieval_proposals', coalesce((
      select jsonb_agg(jsonb_build_object(
        'proposal_id', p.id, 'proposal_type', p.proposal_type, 'payload', p.payload,
        'reason', p.reason, 'proposer_actor_id', p.proposer_id, 'version', p.version,
        'proposed_at', p.created_at
      ) order by p.created_at asc, p.id)
      from retrieval_proposal p
      where p.status = 'pending'
    ), '[]'::jsonb)
  )
$$;

grant execute on function ops.read_governance_queue() to carr_reader;
grant execute on function ops.read_governance_queue() to carr_writer;
grant execute on function ops.read_governance_queue() to carr_authority;

commit;

do $$
declare def text;
begin
  if to_regprocedure('ops.read_governance_queue()') is null then
    raise exception '0345 FAILED: ops.read_governance_queue is missing';
  end if;
  select pg_get_functiondef('ops.read_governance_queue()'::regprocedure) into def;
  if def not like '%pending_rule_approvals%'
     or def not like '%pending_guidance_import_batches%'
     or def not like '%pending_retrieval_proposals%' then
    raise exception '0345 FAILED: ops.read_governance_queue is missing one of its three lanes';
  end if;
  if def not like '%security definer%' and def not like '%SECURITY DEFINER%' then
    raise exception '0345 FAILED: ops.read_governance_queue is not SECURITY DEFINER';
  end if;
  if not has_function_privilege('carr_reader','ops.read_governance_queue()'::regprocedure,'execute') then
    raise exception '0345 FAILED: carr_reader lacks execute on ops.read_governance_queue';
  end if;
  -- It must actually be callable and return the exact three keys -- proves the
  -- grant and the function body both work end to end, not just that they exist.
  if not (select r ?& array['pending_rule_approvals','pending_guidance_import_batches','pending_retrieval_proposals']
          from (select ops.read_governance_queue() as r) s) then
    raise exception '0345 FAILED: ops.read_governance_queue did not return all three lanes at call time';
  end if;
end $$;
