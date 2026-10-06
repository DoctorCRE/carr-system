-- 0477_assurance_owner_acceptance_bounded_decision_window.sql
-- doctrine: carr-production-maturity-baseline
--
-- FORWARD FIX for an unwinnable race in the owner-acceptance path.
--
-- 0451 required the caller's decided_at to satisfy
--     decided_at is not distinct from date_trunc('second', now_at)
-- where now_at is read INSIDE the function. A caller cannot satisfy that: it
-- reads the clock in one round trip and calls in the next, so whenever those
-- two statements straddle a second boundary the value it sends is one second
-- stale and the write refuses. A slow runner widens the window, which is why
-- it read as a load-related flake and was re-run rather than fixed, twice. The
-- defect is present at any speed, and it sits on the same path Joe's own
-- acceptance of assurance evidence travels, so a genuine acceptance could
-- refuse at random with an input-invalid refusal naming decided_at and nothing
-- actionable for the operator.
--
-- Observed 2026-09-02, run 33666079689 on pull request 854: four A3a labels
-- failed together, being 'Joe owner acceptance persists separately', 'owner
-- exact replay is idempotent', 'owner key reuse with changed content refuses
-- [actual=None; causal=None]', and 'owner two-connection exact replay is
-- serialized'. That is one break and three consequences: the first acceptance
-- never persists, so the replay cannot be idempotent, the key-reuse refusal has
-- no prior row to conflict with, and the serialization test has nothing to
-- serialize. Filed as defect 9a6d13d2-b046-4296-bed8-2967d5830d69.
--
-- WHAT CHANGES: one predicate. The exact-second equality becomes a one-second
-- tolerance, so the window is now
--     finished_at <= decided_at <= now_at,
--     decided_at >= now_at - 1 second,
--     decided_at <= snapshot_valid_until.
-- A caller that reads the clock and calls in the next round trip is inside the
-- window whether or not it crossed a second boundary.
--
-- WHY ONE SECOND AND NOT AN OPEN LOWER BOUND: removing the lower edge entirely
-- would stop the gate's 'backdated owner decision refuses' case from refusing,
-- because that case backdates by two seconds and would otherwise sit inside the
-- allowed range. One second is the smallest tolerance that admits the honest
-- round-trip and still refuses that two-second backdate, so the negative test
-- keeps its meaning instead of being loosened to fit the fix.
--
-- STILL REFUSED, unchanged: a decision before the evidence finished, a decision
-- in the future, a decision past the manifest window, and a decision backdated
-- by more than one second. Second-granularity stays available to callers that
-- want it; it is simply no longer demanded, because requiring a caller to
-- predict the server's current second is not a contract any client can honour.
--
-- NOT WIDENED: no new authority, no change to who may accept, and no change to
-- idempotency, digests, replay semantics, or the separate serialization guard.

create or replace function ops.record_assurance_owner_acceptance(
  p_review_manifest_id uuid,p_evidence_id uuid,p_decision text,p_acceptance jsonb,
  p_acceptance_digest text,p_idempotency_key uuid
) returns jsonb language plpgsql volatile security definer
set search_path=pg_catalog,ops,public as $$
declare authority_slug text; context jsonb; actor_row public.actor%rowtype;
        m ops.assurance_execution_manifest%rowtype; ev ops.assurance_evidence_extension%rowtype;
        em ops.assurance_execution_manifest%rowtype; prior ops.assurance_owner_acceptance_fact%rowtype;
        actual text; inserted ops.assurance_owner_acceptance_fact%rowtype;
        l ops.canonical_ownership_lease%rowtype; decided_at timestamptz; finished_at timestamptz;
        now_at timestamptz; lineage_current jsonb; lease_probe uuid;
begin
  perform pg_advisory_xact_lock(hashtextextended('assurance-owner-door',0));
  authority_slug:=ops.authority_actor_slug();
  context:=ops.canonical_ownership_context();
  if not coalesce((context->>'ok')::boolean,false) then return context; end if;
  select * into actor_row from public.actor where slug=authority_slug and kind='human' and active for share;
  if authority_slug<>all(array['joe','dell']) or actor_row.id is null
     or context->>'actor_slug' is distinct from authority_slug
     or (context->>'actor_id')::uuid is distinct from actor_row.id then
    return ops.assurance_refusal('OWNER_IDENTITY_MISMATCH','owner.identity',
      jsonb_build_object('authority_slug',authority_slug,'context_actor_slug',authority_slug),
      jsonb_build_object('authority_context_equal',false));
  end if;
  select lease_id into lease_probe from ops.assurance_execution_manifest
   where id=p_review_manifest_id;
  if lease_probe is not null then
    perform pg_advisory_xact_lock(hashtextextended('assurance-lease-scan',0));
    lineage_current:=ops.assurance_lease_lineage_current(lease_probe,clock_timestamp());
    if not coalesce((lineage_current->>'ok')::boolean,false) then return lineage_current; end if;
    lock table ops.canonical_ownership_lease in share mode;
    lineage_current:=ops.assurance_lease_lineage_current(lease_probe,clock_timestamp());
    if not coalesce((lineage_current->>'ok')::boolean,false) then return lineage_current; end if;
  end if;
  now_at:=clock_timestamp();
  select * into m from ops.assurance_execution_manifest where id=p_review_manifest_id for key share;
  select * into ev from ops.assurance_evidence_extension where id=p_evidence_id for key share;
  select * into em from ops.assurance_execution_manifest where id=ev.manifest_id for key share;
  select * into l from ops.canonical_ownership_lease where id=m.lease_id;
  if not ops.assurance_all_tokens_absent(jsonb_build_array(
       p_acceptance,to_jsonb(p_decision),to_jsonb(p_acceptance_digest),to_jsonb(p_idempotency_key))) then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','assurance.token_nondisclosure',
      '"lease token absent from owner acceptance"'::jsonb,'"token_present"'::jsonb);
  end if;
  if p_decision is null or p_decision<>all(array['accept','hold','reject']) or p_idempotency_key is null
     or m.id is null or ev.id is null
     or not coalesce(ops.assurance_exact_object(p_acceptance,array[
       'schema_version','manifest_hash','evidence_digest','decision','owner_acceptance',
       'independent_review','actor_ref','session_ref','host_ref','reason','decided_at']),false)
     or jsonb_typeof(p_acceptance->'schema_version') is distinct from 'string'
     or p_acceptance->>'schema_version' is distinct from 'assurance-owner-acceptance.v1'
     or p_acceptance->'owner_acceptance' is distinct from 'true'::jsonb
     or p_acceptance->'independent_review' is distinct from 'false'::jsonb
     or exists(select 1 from unnest(array[
          'manifest_hash','evidence_digest','decision','actor_ref','session_ref','host_ref']) key
          where jsonb_typeof(p_acceptance->key) is distinct from 'string')
     or jsonb_typeof(p_acceptance->'reason')<>'string'
     or not coalesce(btrim(p_acceptance->>'reason')<>'',false)
     or not ops.assurance_timestamp_valid(p_acceptance->>'decided_at') then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','owner_acceptance','"closed assurance-owner-acceptance.v1"'::jsonb,'"invalid"'::jsonb);
  end if;
  actual:=ops.assurance_digest(p_acceptance);
  if p_acceptance_digest is distinct from actual then
    return ops.assurance_refusal('ASSURANCE_DIGEST_MISMATCH','acceptance_digest',to_jsonb(actual),to_jsonb(p_acceptance_digest));
  end if;
  if m.repository_stage is distinct from 'review' or em.repository_stage is distinct from 'post_commit'
     or m.repository_commit_sha is distinct from em.repository_commit_sha or m.repository_tree_sha is distinct from em.repository_tree_sha
     or m.work_request_id is distinct from em.work_request_id or m.accepted_plan_id is distinct from em.accepted_plan_id
     or m.slice_plan_id is distinct from em.slice_plan_id or m.slice_ref is distinct from em.slice_ref
     or m.lease_id is distinct from em.lease_id or m.fencing_generation is distinct from em.fencing_generation
     or m.manifest->'slice' is distinct from em.manifest->'slice'
     or m.organization_tenant_id is distinct from context->>'tenant'
     or p_acceptance->>'manifest_hash' is distinct from m.manifest_hash
     or p_acceptance->>'evidence_digest' is distinct from ev.evidence_digest
     or p_acceptance->>'decision' is distinct from p_decision
     or p_acceptance->>'actor_ref' is distinct from 'actor:'||authority_slug
     or p_acceptance->>'session_ref' is distinct from context->>'session_ref'
     or p_acceptance->>'host_ref' is distinct from context->>'host_ref' then
    return ops.assurance_refusal('OWNER_IDENTITY_MISMATCH','owner_acceptance.lineage','"exact authority/context/review-stage-manifest/evidence binding"'::jsonb,'"mismatch"'::jsonb);
  end if;
  if now_at>=m.snapshot_valid_until or l.state is distinct from 'active'
     or l.expires_at<=now_at then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','owner_acceptance.decided_at',
      '"live lease and review manifest window"'::jsonb,'"expired"'::jsonb);
  end if;
  select * into prior from ops.assurance_owner_acceptance_fact where idempotency_key=p_idempotency_key;
  if found then
    if prior.review_manifest_id=p_review_manifest_id and prior.evidence_id=p_evidence_id
       and prior.decision=p_decision and prior.acceptance=p_acceptance
       and prior.acceptance_digest=p_acceptance_digest and prior.owner_actor_id=actor_row.id then
      return jsonb_build_object('ok',true,'acceptance_id',prior.id,'decision',prior.decision,'replayed',true);
    end if;
    return ops.assurance_refusal('IDEMPOTENCY_CONFLICT','assurance_owner_acceptance_fact.idempotency_key','"exact prior request"'::jsonb,'"changed request"'::jsonb);
  end if;
  decided_at:=(p_acceptance->>'decided_at')::timestamptz;
  finished_at:=(ev.evidence#>>'{timestamps,finished_at}')::timestamptz;
  if decided_at<finished_at or decided_at>now_at
     or decided_at<now_at-interval '1 second'
     or decided_at>m.snapshot_valid_until then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','owner_acceptance.decided_at',
      '"evidence finished_at <= decided_at <= live manifest window"'::jsonb,'"invalid"'::jsonb);
  end if;
  now_at:=clock_timestamp();
  if now_at>=m.snapshot_valid_until then
    return ops.assurance_refusal('ASSURANCE_SNAPSHOT_EXPIRED','manifest.snapshot_valid_until',
      to_jsonb(m.snapshot_valid_until),to_jsonb(now_at));
  end if;
  if l.expires_at<=now_at then
    return ops.assurance_refusal('ASSURANCE_BINDING_STALE','lease.currentness',
      '"active unexpired canonical lease at insert"'::jsonb,'"stale"'::jsonb);
  end if;
  insert into ops.assurance_owner_acceptance_fact(
    organization_tenant_id,review_manifest_id,evidence_id,owner_actor_id,owner_actor_slug,
    owner_session_ref,owner_host_ref,decision,acceptance_digest,acceptance,idempotency_key,created_at)
  values(context->>'tenant',p_review_manifest_id,p_evidence_id,actor_row.id,authority_slug,
    context->>'session_ref',context->>'host_ref',p_decision,p_acceptance_digest,p_acceptance,
    p_idempotency_key,now_at)
  returning * into inserted;
  return jsonb_build_object('ok',true,'acceptance_id',inserted.id,'decision',inserted.decision,'replayed',false);
exception when invalid_text_representation or numeric_value_out_of_range or null_value_not_allowed
                    or check_violation or foreign_key_violation or unique_violation then
  return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','owner_acceptance.typed_binding','"well-typed exact acceptance"'::jsonb,'"invalid"'::jsonb);
end $$;
