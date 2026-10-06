-- 0505_gate_zero_tagged_digest_and_candidate_reads.sql
-- THREE FORWARD-ONLY CORRECTIONS TO THE GATE ZERO OUTCOME SURFACE, each one a
-- blocking finding of the outside review that REFUSED the third Worker release
-- candidate 79dc6e5374072dff8f92acb24d31e5c447d13b0c on 2026-09-13.
--
-- WHY A NEW FILE AND NOT AN EDIT. Migrations 0502, 0503 and 0504 are APPLIED to
-- Production -- highest applied 0504, applied count 346 -- and the runner records
-- and re-checks each applied file's sha256. An applied file is history; a
-- correction is a new number. Nothing below touches those three files.
--
-- ── (1) THE TAGGED DIGEST (review finding 2) ─────────────────────────────────
--
-- 0502 recorded the outcome digest as a plain canonical-JSON sha256 over the
-- receipt object, and said so in its own comment as a NAMED ASSUMPTION: r7's
-- `receipt_payload_digest_rule` named domain tags for four schemas and was
-- SILENT on `consumer-gate-receipt.v1`, so the plain reading was taken and put on
-- the surface where one line could overturn it.
--
-- IT WAS OVERTURNED. Act 5 of accepted plan PLAN-a5059eb52474-v3
-- (sha256:a5059eb5247418d150a261c5501dcfa38b69b57512c2d44f02356b92c07893bc)
-- amended r7 on 2026-09-13 to declare this schema's payload digest as the TAGGED
-- preimage -- sha256 over the RFC8785/JCS serialization of the two-element array
-- whose first element is the domain-tag string `consumer-gate-receipt.v1` and
-- whose second is the twenty-one-field receipt -- and to state that a plain
-- digest over the receipt alone does NOT satisfy it. All 62
-- normalized-r7-design chunks were resealed and the packet is pinned at
-- 4379c60e9a4fefbcf044f4bc5a34e5a95c90f77b9adf348b6da7475e17d5e6d7. The plan's
-- own planned check 18 names either reader computing digest(receipt) as a
-- FAILURE CONDITION.
--
-- NO BACKFILL IS OWED AND NONE IS ATTEMPTED. ops.gate_zero_read_only_outcome is
-- empty in Production: the live run that writes the first row is act 13 of the
-- same plan and has not happened. A proof below asserts that emptiness rather
-- than assuming it, because a CREATE OR REPLACE that silently left rows carrying
-- a superseded recipe beside rows carrying the declared one is exactly the
-- divergence the readback exists to catch.
--
-- WHAT IS DELIBERATELY *NOT* TAGGED. ops.gate_zero_outcome_candidate_digest is
-- computed over a PROJECTION of the receipt -- five per-run values removed -- and
-- that projection is not a consumer-gate-receipt.v1, so r7's rule does not speak
-- about it. Tagging a projection with the schema name of the thing it is not
-- would be the misstatement. It stays plain here and plain in the gateway, and it
-- remains an idempotency comparison key rather than evidence.
--
-- ── (2) THE RETRY CONVERGES (review finding 3) ───────────────────────────────
--
-- The outcome row and its audit event are written by two different login roles
-- and therefore two different transactions; the seat's commits first. An outer
-- failure after it leaves an outcome with no event, and 0502's writer then
-- RAISED on any retry whose candidate-scoped projection had moved -- so the very
-- retry meant to heal the missing event could not run. The fallback branch now
-- returns the recorded row instead of raising. Nothing is replaced, no trigger is
-- weakened, and the full reasoning is inside the function below, beside the line
-- it replaces.
--
-- ── (3) THE READS A CANDIDATE FILING NEEDS (review finding 1) ────────────────
--
-- Standing-rule amendment 9(c) requires the release-candidate record to be filed
-- by the deploy wrapper under the AUTHORITY identity, because the Gate Zero seam
-- store reads a subject maker only out of a row whose GENERATED
-- `maker_authority_verified` column is true -- which 0504 makes true exactly when
-- the filing login was carr_authority_joe or carr_authority_dell. Filed on the
-- ledger writer, as the candidate was, 0504 marks the row unauthenticated and the
-- store correctly ignores it. That is the state the reviewer refused.
--
-- 0503 ALREADY ADMITTED THE WRITE HALF: `grant insert on table ops.release to
-- carr_authority`, inside the v26 mutation-registry successor that seals it,
-- plus the two column-scoped selects ops.release's INVOKER-RIGHTS completion
-- trigger reads. What was missing is the two reads the FILING COMMAND PATH
-- itself performs, and measured on Production on 2026-09-13 both were absent:
--
--     has_table_privilege('carr_authority','ops.service','select')  ->  false
--     has_table_privilege('carr_authority','ops.release','select')  ->  false
--
--   * tools/ops-record.py's service_id() runs `select id from ops.service where
--     key = %s` to resolve --service to its foreign key.
--   * its CANDIDATE_INSERT ends `returning id, release_key, maker_actor,
--     maker_session_user, maker_authority_verified`, and RETURNING requires
--     SELECT on the columns it returns.
--
-- Without both, moving the command onto the authority connection replaces a
-- silently-ignored row with a hard permission failure. They are granted below,
-- COLUMN-SCOPED to exactly those columns and nothing wider, and the width is
-- asserted rather than asserted-about.
--
-- NEITHER GRANT IS A NEW INGRESS, and that is measured rather than hoped: the
-- SIEP-11 mutation census counts only the ROW-CHANGING privileges, so a select
-- moves no category and this file is not a mutation-registry successor. 0503's
-- own header states the same rule for the two selects it carried. A proof below
-- re-asserts that this migration adds no update or delete anywhere.

-- NO EXPLICIT TRANSACTION CONTROL: from 0339 onward tools/migrate.py wraps the
-- whole file in ONE transaction, proof blocks included, so a failing proof below
-- rolls this DDL back with it.

-- ── (1) the digest r7 declares ───────────────────────────────────────────────
-- THE PREIMAGE IS THE TWO-ELEMENT ARRAY, built here with jsonb_build_array so
-- the SQL side and artifact-trust.js's canonicalJson serialize the same
-- structure: ops.portfolio_canonical_json preserves array order and renders the
-- tag through to_jsonb, which is byte-for-byte what
-- `canonicalJson(["consumer-gate-receipt.v1", receipt])` produces.
create or replace function ops.gate_zero_outcome_digest(p_receipt jsonb)
returns text language sql immutable
set search_path = pg_catalog, ops, public
as $$
  select 'sha256:' || encode(public.digest(convert_to(
    ops.portfolio_canonical_json(
      jsonb_build_array('consumer-gate-receipt.v1'::text, p_receipt)),
    'UTF8'), 'sha256'), 'hex')
$$;

comment on function ops.gate_zero_outcome_digest(jsonb) is
  'sha256 over the canonical JSON of ["consumer-gate-receipt.v1", <receipt>] -- the TAGGED preimage r7''s receipt_payload_digest_rule declares for this schema (amended 2026-09-13, packet 4379c60e9a4fefbcf044f4bc5a34e5a95c90f77b9adf348b6da7475e17d5e6d7). The same bytes artifact-trust.js digest([schema, receipt]) hashes. Replaces 0502''s plain digest over the receipt object, which that rule now states does not satisfy it.';

comment on column ops.gate_zero_read_only_outcome.outcome_digest is
  'Recomputed from `receipt` with ops.gate_zero_outcome_digest, which is sha256 over the canonical JSON of the TAGGED two-element array ["consumer-gate-receipt.v1", receipt] as r7''s receipt_payload_digest_rule declares (amended 2026-09-13). Never supplied by a caller. ops.portfolio_canonical_json matches artifact-trust.js canonicalJson byte for byte, over that same array.';

-- ── (2) the writer, with the fallback that converges ─────────────────────────
-- LIFTED FROM 0502 RATHER THAN RETYPED. Every line below except the fallback
-- branch is the text migration 0502 applied to Production; only the branch that
-- raised on a moved projection is replaced, and the reasoning travels with it.
-- The signature, the SECURITY DEFINER property, the owner and the EXECUTE grant
-- are unchanged, so this admits no capability: CREATE OR REPLACE keeps the
-- function's ACL, and the carr_gate_zero_producer-only closure 0502 established
-- stands untouched.

create or replace function ops.gate_zero_record_read_only_outcome(
  p_idempotency_key uuid,
  p_receipt jsonb)
returns uuid language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_actor uuid; v_slug text; v_seat text; v_id uuid;
  v_existing ops.gate_zero_read_only_outcome%rowtype;
  v_digest text; v_field text; v_keys integer; v_identity jsonb; v_identity_keys integer;
begin
  if p_idempotency_key is null then
    raise exception 'recording a Gate Zero read-only outcome requires an idempotency key';
  end if;
  if p_receipt is null or jsonb_typeof(p_receipt) <> 'object' then
    raise exception 'recording a Gate Zero read-only outcome requires one consumer-gate-receipt.v1 object';
  end if;

  -- AUTHORITY FIRST, so nothing below it can be reached by a transaction that
  -- may not write here at all.
  v_actor := ops.gate_zero_producer_actor_id();
  v_seat := ops.gate_zero_producer_seat_holder_ref();
  v_slug := split_part(v_seat, ':', 2);

  -- CLOSED SCHEMA. consumer-gate-receipt.v1 sets additional_properties false and
  -- names twenty-one required fields. An unknown field denies, which is r7's own
  -- rule ("Unknown or missing payload fields deny") and is what makes the digest
  -- a statement about a known shape.
  foreach v_field in array array[
    'gate_id', 'receipt_producer_step_ref', 'subject_digest', 'candidate_digest',
    'policy_digest', 'environment_manifest_digest', 'subject_environment', 'evidence_scope',
    'subject_maker_identity', 'producer_identity', 'evaluator_identity', 'producer_role',
    'independent_oracle_ref', 'oracle_version', 'evidence_ref', 'fixture_set_digest',
    'observed_at', 'ttl_expires_at', 'status', 'comparator', 'negative_admission_result'
  ] loop
    if not (p_receipt ? v_field) then
      raise exception 'the Gate Zero receipt is missing the required consumer-gate-receipt.v1 field %', v_field;
    end if;
  end loop;
  select count(*) into v_keys from jsonb_object_keys(p_receipt);
  if v_keys <> 21 then
    raise exception 'consumer-gate-receipt.v1 is a closed schema: the Gate Zero receipt carries % fields rather than 21', v_keys;
  end if;

  -- THE THREE IDENTITIES, AND r7's SHAPE FOR THEM IS CLOSED TOO.
  -- `authenticated-receipt-identity.v1` sets additional_properties false, names
  -- exactly actor_id, session_ref and authority_class, and gives session_ref a
  -- LOWERCASE pattern with a minimum length. All three clauses are checked here,
  -- and all three are checked again in the gateway: a fourth key inside an
  -- identity object would otherwise travel into the digest unread, which would
  -- make the digest a statement about an open shape.
  --
  -- WHAT THIS COPY CANNOT CHECK, said plainly rather than implied: the record
  -- layer does not know which correlation id the gateway derived for this call,
  -- so "these identities are THIS call's" is the gateway's clause alone
  -- (gate-zero-outcome-store.v5.js, against identity.js's authenticated call).
  -- What is checkable here is the SHAPE, the seat and the self-review rule, and
  -- those are checked here because a writer connection opened outside the
  -- gateway reaches this function and not that one.
  foreach v_field in array array['subject_maker_identity', 'producer_identity', 'evaluator_identity'] loop
    v_identity := p_receipt -> v_field;
    if jsonb_typeof(v_identity) <> 'object' then
      raise exception 'the Gate Zero receipt field % is not an authenticated-receipt-identity.v1 object', v_field;
    end if;
    select count(*) into v_identity_keys from jsonb_object_keys(v_identity);
    if v_identity_keys <> 3
       or not (v_identity ? 'actor_id')
       or not (v_identity ? 'session_ref')
       or not (v_identity ? 'authority_class') then
      raise exception 'authenticated-receipt-identity.v1 is a closed schema: the Gate Zero receipt field % carries % fields rather than exactly actor_id, session_ref and authority_class',
        v_field, v_identity_keys;
    end if;
    if coalesce(v_identity ->> 'actor_id', '') = ''
       or coalesce(v_identity ->> 'authority_class', '') = '' then
      raise exception 'the Gate Zero receipt field % has an empty actor_id or authority_class', v_field;
    end if;
    -- r7's own pattern, character for character. Lowercase only, and at least
    -- nine characters after `session:`.
    if coalesce(v_identity ->> 'session_ref', '') !~ '^session:[a-z0-9][a-z0-9:._/-]{8,199}$' then
      raise exception 'the Gate Zero receipt field % has a session_ref that is not r7''s authenticated-receipt-identity.v1 pattern: %',
        v_field, coalesce(v_identity ->> 'session_ref', '');
    end if;
  end loop;
  if p_receipt -> 'producer_identity' ->> 'actor_id' <> v_slug
     or p_receipt -> 'evaluator_identity' ->> 'actor_id' <> v_slug then
    raise exception 'the Gate Zero receipt names a producer or evaluator other than the staffed seat lane %', v_slug;
  end if;
  if p_receipt -> 'producer_identity' ->> 'authority_class' <> 'review_agent'
     or p_receipt -> 'evaluator_identity' ->> 'authority_class' <> 'review_agent' then
    raise exception 'the Gate Zero receipt names a producer or evaluator authority class other than review_agent';
  end if;
  -- SAME-ACTOR SELF-REVIEW DENIES, in both dimensions r7 names.
  if p_receipt -> 'subject_maker_identity' ->> 'actor_id' = v_slug then
    raise exception 'the Gate Zero receipt names the oracle seat as the maker of its own subject; r7 requires the subject maker to differ from the evaluator';
  end if;
  if p_receipt -> 'subject_maker_identity' ->> 'session_ref'
     = p_receipt -> 'evaluator_identity' ->> 'session_ref' then
    raise exception 'the Gate Zero receipt names one session as both subject maker and evaluator';
  end if;

  -- IDEMPOTENT ON THE CANDIDATE, ATOMICALLY, and the atomicity is the whole
  -- point of the shape (2026-09-13, PR 1014 correction).
  --
  -- THE DEFECT THIS REPLACES. The first draft looked the candidate up, found
  -- nothing, and then inserted. Two runs of the same candidate arriving at once
  -- both missed the lookup, and the loser of the race got a bare
  -- unique_violation on candidate_digest instead of the durable row -- a retry
  -- policy that says "every run kept, a retry collapses onto the row that
  -- exists" turning into an error whenever two writers actually retried at once.
  --
  -- THE SHAPE THAT CANNOT RACE. ONE statement does the insert with the candidate
  -- key as its arbiter, so the conflict is resolved by the index rather than by
  -- a window between two statements: a concurrent inserter BLOCKS on the
  -- speculative insertion, and when the first committer commits the second takes
  -- the DO NOTHING branch and reads the committed row in the fallback select --
  -- which sees it, because each statement in READ COMMITTED takes a fresh
  -- snapshot. If the first transaction rolls back instead, the second inserts.
  -- Either way both callers receive the same durable row and neither receives an
  -- error.
  --
  -- AND A DIFFERENT RECEIPT FOR A RECORDED CANDIDATE STILL CANNOT REPLACE IT.
  -- The fallback returns the immutable first row unconditionally; the gateway
  -- separately reports the recorded and offered projections so changed evidence
  -- remains visible and a retry can heal an audit event the outer transaction
  -- failed to write.
  insert into ops.gate_zero_read_only_outcome (
    idempotency_key, step_ref, receipt_producer_step_ref, gate_id, receipt_schema,
    producer_role, independent_oracle_ref, oracle_version, evidence_scope,
    subject_environment, negative_admission_result, producing_seat_ref, producing_actor_id,
    candidate_digest, subject_digest, policy_digest, environment_manifest_digest,
    fixture_set_digest, evidence_ref, receipt, outcome_digest, candidate_scoped_digest,
    status, comparator, observed_at, ttl_expires_at)
  values (
    p_idempotency_key,
    'step:gate-zero-read-only-outcome',
    p_receipt ->> 'receipt_producer_step_ref',
    p_receipt ->> 'gate_id',
    'consumer-gate-receipt.v1',
    p_receipt ->> 'producer_role',
    p_receipt ->> 'independent_oracle_ref',
    p_receipt ->> 'oracle_version',
    p_receipt ->> 'evidence_scope',
    p_receipt ->> 'subject_environment',
    p_receipt ->> 'negative_admission_result',
    v_seat,
    v_actor,
    p_receipt ->> 'candidate_digest',
    p_receipt ->> 'subject_digest',
    p_receipt ->> 'policy_digest',
    p_receipt ->> 'environment_manifest_digest',
    p_receipt ->> 'fixture_set_digest',
    p_receipt ->> 'evidence_ref',
    p_receipt,
    ops.gate_zero_outcome_digest(p_receipt),
    ops.gate_zero_outcome_candidate_digest(p_receipt),
    p_receipt ->> 'status',
    p_receipt ->> 'comparator',
    (p_receipt ->> 'observed_at')::timestamptz,
    (p_receipt ->> 'ttl_expires_at')::timestamptz)
  on conflict (candidate_digest) do nothing
  returning id into v_id;
  if v_id is not null then
    return v_id;
  end if;

  -- THE FALLBACK, reached only when the arbiter index already held this
  -- candidate. It is a separate statement and therefore a fresh snapshot, which
  -- is what lets it see a row a concurrent transaction committed while this
  -- insert was blocked on it.
  select * into v_existing from ops.gate_zero_read_only_outcome
   where candidate_digest = p_receipt ->> 'candidate_digest';
  if not found then
    -- Neither inserted nor found: the only way here is the row having been
    -- removed between the two statements, which the append-only triggers refuse.
    -- It is reported rather than retried, because a writer that cannot explain
    -- its own outcome must not invent one.
    raise exception 'the Gate Zero outcome for candidate % was neither inserted nor found; the record layer is in a state this writer cannot account for',
      p_receipt ->> 'candidate_digest';
  end if;
  -- AND THE RECORDED ROW IS RETURNED, EVEN WHEN THIS CALL'S EVIDENCE MOVED
  -- (2026-09-13, the third release candidate's refusal, finding 3). This is the
  -- one behaviour 0505 changes, and it is a change of ANSWER, never of state.
  --
  -- WHAT THE OUTSIDE REVIEWER FOUND. The write of the outcome and the write of
  -- its audit event cannot share a transaction: they authenticate as different
  -- login roles, which is the whole of standing-rule amendment 9. The seat's
  -- transaction therefore commits first, and any failure after it -- the
  -- gateway's own comparison, the writer connection, the process -- leaves a
  -- recorded outcome with NO event. The recovery for that was always "retry, and
  -- the retry converges on the row and writes the event that was lost". It did
  -- not hold: the line that stood here RAISED whenever the retry's
  -- candidate-scoped projection differed from the recorded one, and evidence
  -- legitimately moves between two runs of one candidate -- a check conclusion
  -- lands, a predecessor outcome expires, a verdict flips. So the exact state the
  -- failure produces was the state no retry could repair. The reviewer's words:
  -- "an outcome-without-event state is reachable where subsequent retries using
  -- the now-current evidence cannot converge."
  --
  -- WHAT CONVERGING IS NOT. It is not a replace and it is not a widened
  -- permission. The FIRST receipt stays the stored one, its digest stays the
  -- recorded evidence, and the append-only row and statement triggers are
  -- untouched: nothing in this branch updates, deletes or re-inserts anything,
  -- and this transaction has written no row at all when it reaches here. A
  -- caller offering different evidence is told which outcome it converged onto
  -- rather than being handed a row it may mistake for its own -- the gateway
  -- recomputes both digests over the STORED receipt and reports
  -- `converged_onto_recorded_outcome` beside its own offered value -- and the
  -- NOTICE below puts the same fact in the server log, where an operator reading
  -- back a live run can see that a second run did not bind.
  --
  -- WHY NOT KEEP THE RAISE AND HEAL THE EVENT SOME OTHER WAY. Every other door
  -- is worse: a second writer function is a second EXECUTE grant and a new
  -- mutation capability; letting the gateway write the event off a parsed error
  -- string makes an error message load-bearing; and holding the seat's
  -- transaction open across the event write would let an ordinary writer's
  -- failure roll back an oracle's signature, which 0502 already refused.
  v_digest := ops.gate_zero_outcome_candidate_digest(p_receipt);
  if v_existing.candidate_scoped_digest <> v_digest then
    raise notice 'the Gate Zero outcome for candidate % is already recorded from a run whose projection differs (recorded %, offered %); the recorded row stands and is returned unchanged',
      v_existing.candidate_digest, v_existing.candidate_scoped_digest, v_digest;
  end if;
  return v_existing.id;
end;
$$;

comment on function ops.gate_zero_record_read_only_outcome(uuid,jsonb) is
  'The only way to record a Gate Zero read-only outcome. The producing seat, the actor and the outcome digest are all derived; the receipt and an idempotency key are the only parameters. Refuses every transaction except the staffed non-human oracle seat, refuses a receipt whose producer or evaluator is not that seat, refuses same-actor or same-session self-review, enforces authenticated-receipt-identity.v1''s closed three-field shape on each of the three identities, and is idempotent on the candidate digest ATOMICALLY -- one insert arbitrated by the candidate key, with a fallback select -- so two writers racing the same candidate both receive the same durable row rather than one of them receiving a unique_violation. From 0505 the fallback returns the recorded row UNCONDITIONALLY, including when the retry''s candidate-scoped projection has moved: the first receipt stays stored, stays digested and is never replaced, and the caller is told which outcome it converged onto instead of being refused -- which is what lets a retry write an audit event a failed outer transaction lost. The full outcome_digest stored beside the row is the TAGGED digest r7 declares.';

-- ── (3) the two column-scoped reads a candidate filing performs ──────────────
-- ops.service, read by tools/ops-record.py's service_id() to resolve --service.
-- Two columns: the one it filters on and the one it returns.
grant select (id, key) on table ops.service to carr_authority;

-- ops.release, read by the same command's RETURNING clause. Five columns, and
-- they are the five that clause names -- not the table, because the authority
-- bundle has never held a whole-table select here and this file is not the place
-- to give it one. maker_authority_verified is among them and is GENERATED, so
-- reading it back is the only thing any role can do with it.
grant select (id, release_key, maker_actor, maker_session_user,
              maker_authority_verified)
  on table ops.release to carr_authority;

do $gate_zero_candidate_filing_reads$
declare
  v_col        text;
  v_row_count  integer;
  v_attgenerated char;
  v_acl_probe  text;
  v_forbidden_execute boolean;
begin
  -- 0. THE ROW THIS MIGRATION EXISTS TO MAKE READABLE IS STILL UNWRITTEN, so
  --    the digest replaced above has no persisted value to contradict. Asserted,
  --    not assumed: a stored row under the superseded recipe beside one under the
  --    declared recipe is precisely the divergence the readback is for.
  select count(*) into v_row_count from ops.gate_zero_read_only_outcome;
  if v_row_count <> 0 then
    raise exception '0505 FAILED: % Gate Zero outcome row(s) already carry the superseded plain digest; replacing the recipe under them would leave two recipes in one table',
      v_row_count;
  end if;

  -- 1. THE TAGGED PREIMAGE IS WHAT THE FUNCTION NOW COMPUTES, proven against the
  --    array built independently here rather than against a pasted constant. A
  --    pinned hex string would pass if the function hashed the wrong bytes and
  --    somebody pasted the wrong bytes' hash.
  if ops.gate_zero_outcome_digest('{"a":1}'::jsonb) <>
     'sha256:' || encode(public.digest(convert_to(
       ops.portfolio_canonical_json(jsonb_build_array('consumer-gate-receipt.v1'::text, '{"a":1}'::jsonb)),
       'UTF8'), 'sha256'), 'hex') then
    raise exception '0505 FAILED: the outcome digest is not the tagged preimage';
  end if;
  -- AND IT IS NOT THE PLAIN ONE. The mutation this file exists to make, asserted
  -- as a refusal rather than described: if the tag were dropped the two would be
  -- equal, and this line would be the one that went red.
  if ops.gate_zero_outcome_digest('{"a":1}'::jsonb) =
     'sha256:' || encode(public.digest(convert_to(
       ops.portfolio_canonical_json('{"a":1}'::jsonb), 'UTF8'), 'sha256'), 'hex') then
    raise exception '0505 FAILED: the outcome digest still equals the untagged digest r7 refuses';
  end if;
  -- AND THE CANDIDATE KEY IS STILL UNTAGGED, on purpose: it digests a projection
  -- that is not a consumer-gate-receipt.v1 and that r7's rule does not cover.
  if ops.gate_zero_outcome_candidate_digest('{"observed_at":"x","ttl_expires_at":"y","s":1}'::jsonb) <>
     'sha256:' || encode(public.digest(convert_to(
       ops.portfolio_canonical_json(ops.gate_zero_outcome_candidate_projection(
         '{"observed_at":"x","ttl_expires_at":"y","s":1}'::jsonb)), 'UTF8'), 'sha256'), 'hex') then
    raise exception '0505 FAILED: the candidate comparison key stopped being the plain digest over the projection';
  end if;

  -- 2. THE WRITER IS STILL THE SEAT'S AND NOTHING ELSE'S. CREATE OR REPLACE
  --    preserves an ACL, and "preserves" is the kind of claim worth measuring
  --    once rather than trusting forever.
  if not has_function_privilege('carr_gate_zero_producer',
        'ops.gate_zero_record_read_only_outcome(uuid,jsonb)', 'execute') then
    raise exception '0505 FAILED: the producer seat lost EXECUTE on its own writer';
  end if;
  -- Run the same census over its clean state, a real catalog mutation, and the
  -- restored state. PUBLIC is not a pg_roles row: aclexplode represents it as
  -- grantee OID 0, which is why the LEFT JOIN and explicit zero test below are
  -- load-bearing rather than stylistic.
  foreach v_acl_probe in array array['baseline', 'public-mutation', 'restored'] loop
    if v_acl_probe = 'public-mutation' then
      execute 'grant execute on function ops.gate_zero_record_read_only_outcome(uuid,jsonb) to public';
    elsif v_acl_probe = 'restored' then
      execute 'revoke execute on function ops.gate_zero_record_read_only_outcome(uuid,jsonb) from public';
    end if;
    select exists (
      select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
      cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) acl
      left join pg_roles g on g.oid = acl.grantee
      where n.nspname = 'ops' and p.proname = 'gate_zero_record_read_only_outcome'
        and acl.privilege_type = 'EXECUTE'
        and (
          acl.grantee = 0 -- PUBLIC has no pg_roles row; aclexplode uses OID 0.
          or g.oid is null -- Fail closed if any other ACL grantee is unresolved.
          or (g.rolname <> 'carr_gate_zero_producer' and acl.grantee <> p.proowner)
        )
    ) into v_forbidden_execute;
    if v_acl_probe in ('baseline', 'restored') and v_forbidden_execute then
      raise exception '0505 FAILED: a role other than the producer seat or owner holds EXECUTE on the writer';
    elsif v_acl_probe = 'public-mutation' and not v_forbidden_execute then
      raise exception '0505 FAILED: the writer EXECUTE census did not catch its GRANT EXECUTE TO PUBLIC mutation';
    end if;
  end loop;
  -- AND IT IS STILL SECURITY DEFINER, which is what makes session_user the
  -- connection's own login rather than the definer's.
  if not exists (
    select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'ops' and p.proname = 'gate_zero_record_read_only_outcome'
      and p.prosecdef) then
    raise exception '0505 FAILED: the writer is no longer SECURITY DEFINER';
  end if;

  -- 3. THE APPEND-ONLY TRIGGERS ARE UNTOUCHED. The fallback now returns a row
  --    instead of raising, and the one thing that must not have quietly bought
  --    is a weaker immutability guarantee.
  foreach v_col in array array['gate_zero_read_only_outcome_append_only',
                               'gate_zero_read_only_outcome_append_only_stmt'] loop
    if not exists (
      select 1 from pg_trigger t
      where t.tgrelid = 'ops.gate_zero_read_only_outcome'::regclass
        and t.tgname = v_col and not t.tgisinternal) then
      raise exception '0505 FAILED: the append-only trigger % is absent', v_col;
    end if;
  end loop;

  -- 4. THE TWO READS EXIST, AT EXACTLY THE WIDTH GRANTED. Column-scoped, proven
  --    per column, with a column OUTSIDE each list asserted absent -- the
  --    interlock 0117 established, because a bug that flattened column grants
  --    into table grants passes every positive test and is caught only here.
  foreach v_col in array array['id', 'key'] loop
    if not has_column_privilege('carr_authority', 'ops.service', v_col, 'select') then
      raise exception '0505 FAILED: carr_authority cannot read ops.service.%', v_col;
    end if;
  end loop;
  if has_table_privilege('carr_authority', 'ops.service', 'select') then
    raise exception '0505 FAILED: the ops.service read is a whole-table grant, not the two columns granted';
  end if;
  foreach v_col in array array['id', 'release_key', 'maker_actor',
                               'maker_session_user', 'maker_authority_verified'] loop
    if not has_column_privilege('carr_authority', 'ops.release', v_col, 'select') then
      raise exception '0505 FAILED: carr_authority cannot read ops.release.%', v_col;
    end if;
  end loop;
  if has_table_privilege('carr_authority', 'ops.release', 'select') then
    raise exception '0505 FAILED: the ops.release read is a whole-table grant, not the five columns granted';
  end if;
  if has_column_privilege('carr_authority', 'ops.release', 'plan_hash', 'select') then
    raise exception '0505 FAILED: the ops.release read reaches a column this migration did not grant';
  end if;

  -- 5. AND NO ROW-CHANGING PRIVILEGE MOVED, which is the measurement behind
  --    "this file is not a mutation-registry successor". The insert 0503
  --    admitted is expected and named; anything else on either relation is not.
  if exists (
    select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace
    cross join lateral aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) acl
    join pg_roles g on g.oid = acl.grantee
    where g.rolname = 'carr_authority' and n.nspname = 'ops'
      and c.relname in ('release', 'service')
      and acl.privilege_type in ('UPDATE', 'DELETE', 'TRUNCATE')
  ) then
    raise exception '0505 FAILED: a row-changing privilege beyond 0503''s insert reached carr_authority';
  end if;
  if has_table_privilege('carr_authority', 'ops.service', 'insert') then
    raise exception '0505 FAILED: ops.service gained a write grant this migration never asked for';
  end if;

  -- 6. THE COLUMN THE WHOLE FILING PATH TURNS ON IS STILL UNWRITABLE. The reads
  --    above exist so a filing session can READ maker_authority_verified back;
  --    if it had become writable, reading it back would prove nothing.
  select attgenerated into v_attgenerated from pg_attribute
   where attrelid = 'ops.release'::regclass and attname = 'maker_authority_verified';
  if v_attgenerated is distinct from 's' then
    raise exception '0505 FAILED: maker_authority_verified is no longer a stored generated column';
  end if;
end $gate_zero_candidate_filing_reads$;
