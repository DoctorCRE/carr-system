-- 0616_f03_retire_legacy_slice_plan_registration.sql
--
-- V5-F03: refuse NEW registrations of engineering-slice-plan.v1.  Forward-only.
--
-- WHY.  A v1 plan carries no design_contract, so it records no per-slice
-- rationale, no code/model allocation and no reuse/replace seam decision.  0507a
-- held engineering-slice-plan.v2 to that contract at both registration seams but
-- still accepted a fresh v1 registration, which let any producer skip the whole
-- contract by declaring the older version (live proof 2026-09-25: the fixture
-- cases A05 and B01 in mcp-server/test/f03-plan-ownership-validator-postgres.sql
-- pinned that acceptance).
--
-- WHAT CHANGES.  Exactly one function, ops.engineering_register_slice_plan,
-- re-issued from migrations/0507a_engineering_slice_plan_validators.sql SECTION 3
-- verbatim except the one block marked "V5-F03 v1 retirement".  Same name,
-- argument names and types, return type, LANGUAGE, SECURITY DEFINER and
-- search_path.  CREATE OR REPLACE keeps the existing owner and ACL, so the 0310
-- EXECUTE grant to carr_writer is neither widened nor narrowed.
--
-- WHAT DOES NOT CHANGE, AND WHY THAT IS THE WHOLE DESIGN.
--   * ops.engineering_slice_plan_refusal keeps accepting v1.  It is the SQL twin
--     of requirePlan, which cannot tell a registration from a stored read (see
--     the V5-F03 note in mcp-server/src/engineering-runtime.js), and the receipt
--     seam calls it over stored plans.
--   * ops.engineering_record_slice_receipt, ops.canonical_ownership_plan_dependencies
--     and ops.canonical_ownership_dependency_state are untouched, so every stored
--     v1 plan reads, admits and receipts exactly as before.
--   * No stored plan, receipt, envelope or lease row is read, rewritten or
--     re-validated.
--   * The receipt seam gets no created_at cutoff.  carr_writer, carr_reader and
--     carr_jobs hold only SELECT on ops.engineering_slice_plan, so this function
--     is the only application insert door; with it refusing v1, a v1 row
--     created after this migration can only come from a privileged direct
--     insert, which is not an application door.  A cutoff would also re-issue a
--     350-line function for a set that is empty by construction.
-- ---------------------------------------------------------------------------

create or replace function ops.engineering_register_slice_plan(
  p_work_request text, p_plan jsonb, p_plan_digest text, p_idempotency_key uuid
)
returns ops.engineering_slice_plan
language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare source jsonb; row ops.engineering_slice_plan%rowtype;
        -- V5-F03: the whole-plan refusal token, NULL when the plan is accepted.
        plan_refusal text;
begin
  source := ops.engineering_admission_source(p_work_request);
  if source is null then raise exception 'engineering admission requires a current accepted ready plan'; end if;
  if p_plan_digest !~ '^sha256:[0-9a-f]{64}$' then raise exception 'invalid engineering slice plan digest'; end if;
  if p_plan->'work_request'->>'id' <> source->'work_request'->>'id'
     or (p_plan->'work_request'->>'state_version')::integer <> (source->'work_request'->>'version')::integer
     or p_plan->'accepted_plan_revision'->>'id' <> source->'accepted_plan'->>'plan_ref'
     or p_plan->'accepted_plan_revision'->>'digest' <> source->'accepted_plan'->>'digest'
     or (p_plan->'accepted_plan_revision'->>'revision')::integer <> (source->'accepted_plan'->>'revision')::integer
     or p_plan->>'plan_digest' <> p_plan_digest then
    raise exception 'engineering slice plan is not bound to the exact accepted Work Request and plan';
  end if;
  -- V5-F03: the whole-plan check the database has never had.  It runs AFTER the
  -- five binding comparisons above, so every existing refusal keeps its exact
  -- text and its exact precedence, and it runs BEFORE the insert, so a refused
  -- plan is never written.  It is a pure JSON predicate: it takes no lock,
  -- samples no clock and reads no table, so the transaction's behavior around
  -- it is unchanged.
  --
  -- For engineering-slice-plan.v1 this returns NULL as soon as it has checked
  -- the top-level shape and version, so a v1 registration is unchanged; see THE
  -- v1 BOUNDARY in the header for the two narrow exceptions.  For
  -- engineering-slice-plan.v2 it is the full requirePlan predicate, including
  -- the plan_digest-binds-content check, which is what closes the direct
  -- carr_writer bypass of the caller-side validators for plans stored FROM NOW
  -- ON.  Plans already stored are covered at the receipt seam by the same
  -- function; see REGISTRATION ALONE IS NOT ENOUGH in the header.
  --
  -- It runs before the ON CONFLICT ... DO NOTHING replay below, so re-asserting
  -- an already-stored malformed v2 plan under its original idempotency key
  -- raises rather than replaying.  See consequence (ii) in the header.
  plan_refusal := ops.engineering_slice_plan_refusal(p_plan);
  if plan_refusal is not null then
    raise exception 'engineering slice plan is not a valid typed slice plan: %', plan_refusal;
  end if;
  -- V5-F03 v1 retirement (0616): a NEW registration must carry the successor
  -- version.  This is the database twin of requireRegistrablePlanVersion in
  -- mcp-server/src/engineering-runtime.js, and like it it lives only at this
  -- registration seam, never in ops.engineering_slice_plan_refusal: that
  -- predicate also validates already-stored plans, and a stored v1 plan must
  -- keep reading, admitting and receipting exactly as before.  It runs after
  -- the whole-plan check, so a malformed plan keeps its existing refusal, and
  -- before the ON CONFLICT replay and the insert, so a refused plan is never
  -- written and a v1 plan cannot be re-asserted under an old key either.
  if p_plan->>'schema_version' is distinct from 'engineering-slice-plan.v2' then
    raise exception 'engineering slice plan version is not registrable: %; new plans register as engineering-slice-plan.v2',
      coalesce(p_plan->>'schema_version','<missing>');
  end if;
  insert into ops.engineering_slice_plan
    (work_request_id,accepted_plan_id,accepted_plan_hash,work_request_version,plan_digest,plan,idempotency_key)
  values (regexp_replace(source->'work_request'->>'id', '^wr:', '')::uuid,
          (source->'accepted_plan'->>'record_id')::uuid,
          source->'accepted_plan'->>'digest',(source->'work_request'->>'version')::integer,
          p_plan_digest,p_plan,p_idempotency_key)
  on conflict (idempotency_key) do nothing
  returning * into row;
  if row.id is null then
    select * into row from ops.engineering_slice_plan where idempotency_key=p_idempotency_key;
    if row.plan_digest <> p_plan_digest or row.plan <> p_plan then
      raise exception 'engineering slice plan idempotency key was reused with different content';
    end if;
  end if;
  return row;
end $$;

comment on function ops.engineering_register_slice_plan(text,jsonb,text,uuid) is
  'Register one typed Engineering Slice Plan as an immutable projection of the exact '
  'accepted sourced plan. Every 0310 binding, idempotency and refusal behavior is '
  'unchanged; the plan is held to the whole-plan contract by '
  'ops.engineering_slice_plan_refusal, which closes the direct carr_writer bypass of the '
  'caller-side validators, and since 0616 only engineering-slice-plan.v2 may be newly '
  'registered. Stored engineering-slice-plan.v1 rows are not touched and keep their read path.';
