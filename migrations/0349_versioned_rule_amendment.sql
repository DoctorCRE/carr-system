-- 0349_versioned_rule_amendment.sql
--
-- WR-000019 slice S10, part 1 of 3 (Obedience & Autonomy heavy build).
--
-- WHY THIS EXISTS. An APPROVED rule's text has been immutable since migration
-- 0194/0228: ops.require_rule_admission's trigger permits exactly two status
-- transitions on an active-or-proposed rule (proposed->active in
-- ops.approve_rule, proposed/active->retired in ops.retire_rule) and refuses
-- every other UPDATE outright. That is correct for SUBSTANCE — an old receipt
-- must never appear to bless new meaning — but it left no way to fix a plain
-- wording defect in an already-approved rule. The only escape was
-- retire-and-reteach under a brand-new id: every citation of the old id broke,
-- created_at and the activation history were lost, and human_quote had to be
-- re-said even when nothing about the partner's testimony had changed. That
-- shape produced a family of nine near-duplicate rules, each one a restatement
-- of an existing rule under a fresh id because nobody could correct the
-- original in place.
--
-- WHAT THIS ADDS, deliberately narrow. A new, guarded, receipted path that
-- changes ONLY rule.statement on an already-taught rule (proposed OR active),
-- refuses on a retired rule, requires the same Joe-authority principal as
-- ops.approve_rule and ops.retire_rule, and leaves an append-only trail
-- (ops.rule_amendment_receipt) that hashes the PRIOR statement so a reader can
-- always prove what the words used to be. human_quote, scope, taught_by,
-- personal_to, supersedes, activation and retirement fields are untouched —
-- amendment corrects wording, never testimony, audience or history.
--
-- ops.approve_rule and ops.retire_rule are NOT edited by this migration and
-- carry no new branch of their own; this is a THIRD, separate guarded path
-- installed beside them, exactly as WR-000019 S10 requires.
--
-- THE PART THAT IS EASY TO MISS: ops.applicable_rules() is the function that
-- compiles what a session is actually told to obey, and it requires the live
-- rule's statement to hash-match the exact text ops.approve_rule's receipt
-- was written against. Left alone, amending an ACTIVE rule's wording would
-- silently drop it out of every future session's recitation the moment its
-- statement changed -- the rule would still read "active" in every board and
-- audit, while enforcing nothing. That is a worse failure than the one this
-- migration sets out to fix: at least retire-and-reteach was visible. So
-- ops.rule_amendment_reaches() proves an unbroken, hash-chained path from the
-- approval receipt's frozen statement to the rule's CURRENT statement through
-- one or more ops.rule_amendment_receipt rows, and ops.applicable_rules()'s
-- match condition accepts either the original exact match (unchanged
-- behaviour for every rule never amended) or a proven chain. Forging a chain
-- is not possible from outside: every receipt's prior_statement_hash is
-- computed by ops.amend_rule_statement itself from the row it holds locked,
-- never taken as caller input.

begin;

-- ── the append-only amendment ledger ────────────────────────────────────────

create table ops.rule_amendment_receipt (
    id uuid default gen_random_uuid() not null,
    idempotency_key text not null,
    rule_id uuid not null,
    rule_version_before integer not null,
    rule_version_after integer not null,
    prior_statement_hash text not null,
    new_statement text not null,
    new_statement_hash text not null,
    amended_by uuid not null,
    rationale text not null,
    contract_hash text not null,
    amended_at timestamp with time zone not null,
    created_at timestamp with time zone default now() not null,
    constraint rule_amendment_receipt_idempotency_key_check check (btrim(idempotency_key) <> ''),
    constraint rule_amendment_receipt_rationale_check check (btrim(rationale) <> ''),
    constraint rule_amendment_receipt_new_statement_check check (btrim(new_statement) <> ''),
    constraint rule_amendment_receipt_rule_version_before_check check (rule_version_before > 0),
    constraint rule_amendment_receipt_version_check check (rule_version_after = rule_version_before + 1),
    constraint rule_amendment_receipt_prior_hash_check check (prior_statement_hash ~ '^[0-9a-f]{64}$'),
    constraint rule_amendment_receipt_new_hash_check check (new_statement_hash ~ '^[0-9a-f]{64}$'),
    constraint rule_amendment_receipt_contract_hash_check check (contract_hash ~ '^[0-9a-f]{64}$'),
    -- A no-op "amendment" that changes nothing is not testimony of a
    -- correction; it is drift with a rationale attached to it after the fact.
    constraint rule_amendment_receipt_not_noop check (prior_statement_hash <> new_statement_hash)
);

alter table ops.rule_amendment_receipt
    add constraint rule_amendment_receipt_pkey primary key (id);
alter table ops.rule_amendment_receipt
    add constraint rule_amendment_receipt_idempotency_key_key unique (idempotency_key);
alter table ops.rule_amendment_receipt
    add constraint rule_amendment_receipt_rule_id_fkey
        foreign key (rule_id) references public.rule(id) on delete restrict;
alter table ops.rule_amendment_receipt
    add constraint rule_amendment_receipt_amended_by_fkey
        foreign key (amended_by) references public.actor(id);

create index rule_amendment_receipt_rule_id_idx
    on ops.rule_amendment_receipt (rule_id, rule_version_before);

comment on table ops.rule_amendment_receipt is
    'Append-only ledger of statement-only amendments to an already-taught rule (proposed or active). Each row hashes the PRIOR statement (tamper-evident chain) and records the new text, the Joe-authority actor, and the rationale. human_quote, scope, taught_by and every activation/retirement field are untouched by this path -- see ops.amend_rule_statement.';

-- Same reuse the retirement ledger already made of the approval ledger's
-- rewrite-refusal trigger function (schema.sql: rule_retirement_receipt_append_only
-- and rule_approval_receipt_append_only both fire ops.refuse_rule_approval_receipt_rewrite()).
-- A third receipt table refusing every UPDATE/DELETE the same way is the same
-- invariant, not a new one, so it reuses the existing function rather than
-- minting a fourth copy of "raise exception append-only".
create trigger rule_amendment_receipt_append_only
    before delete or update on ops.rule_amendment_receipt
    for each row execute function ops.refuse_rule_approval_receipt_rewrite();

grant select on table ops.rule_amendment_receipt to carr_authority;
grant select on table ops.rule_amendment_receipt to carr_jobs;
grant select on table ops.rule_amendment_receipt to carr_reader;
grant select on table ops.rule_amendment_receipt to carr_writer;
-- No INSERT/UPDATE/DELETE grant to any application role: every row is written
-- by ops.amend_rule_statement, SECURITY DEFINER, exactly like the approval and
-- retirement ledgers beside it.

-- ── chain-reachability helper for ops.applicable_rules() ────────────────────
--
-- Proves an unbroken path of amendment receipts from (p_from_version,
-- p_from_hash) -- the approval receipt's frozen version/statement pair -- to
-- (p_to_version, p_to_hash) -- the rule's current version/statement. Ordinary,
-- never-amended rules never call this (the exact-match disjunct in
-- ops.applicable_rules() short-circuits first); it exists purely so an
-- amended rule does not silently vanish from recitation.
create function ops.rule_amendment_reaches(
    p_rule_id uuid, p_from_version integer, p_from_hash text,
    p_to_version integer, p_to_hash text
) returns boolean
    language sql stable
    as $$
  with recursive chain(rule_version_after, new_statement_hash) as (
    select r.rule_version_after, r.new_statement_hash
      from ops.rule_amendment_receipt r
     where r.rule_id = p_rule_id
       and r.rule_version_before = p_from_version
       and r.prior_statement_hash = p_from_hash
    union all
    select nx.rule_version_after, nx.new_statement_hash
      from ops.rule_amendment_receipt nx
      join chain c on nx.rule_version_before = c.rule_version_after
     where nx.rule_id = p_rule_id
  )
  select exists (
    select 1 from chain
     where rule_version_after = p_to_version
       and new_statement_hash = p_to_hash
  )
$$;

comment on function ops.rule_amendment_reaches(uuid, integer, text, integer, text) is
    'True when an unbroken chain of ops.rule_amendment_receipt rows connects the approval receipt''s frozen (version, statement_hash) to the rule''s CURRENT (version, statement_hash). Used only by ops.applicable_rules() so an amended active rule keeps reciting.';

-- ── the guarded amendment function itself ───────────────────────────────────
--
-- Same shape as ops.approve_rule and ops.retire_rule: Joe-authority actor
-- derived from session_user via ops.authority_actor_slug() (never caller
-- input), idempotency replay guarded by an advisory transaction lock, receipt
-- written before the row changes, and the update itself is optimistic
-- (version-guarded) so a race raises rather than silently overwriting.
create function ops.amend_rule_statement(
    p_rule_id uuid, p_new_statement text, p_idempotency_key text, p_reason text
) returns jsonb
    language plpgsql security definer
    set search_path to 'ops', 'public', 'pg_temp'
    as $$
declare
  v_actor_slug text;
  v_actor_id uuid;
  v_rule rule%rowtype;
  v_prior ops.rule_amendment_receipt%rowtype;
  v_receipt ops.rule_amendment_receipt%rowtype;
  v_prior_hash text;
  v_new_hash text;
  v_new_statement text;
  v_contract jsonb;
  v_contract_hash text;
  v_amended_at timestamptz;
begin
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug <> 'joe' then
    raise exception 'system rule amendment requires Joe authority; % may teach and participate but cannot replace Joe approval',
      v_actor_slug;
  end if;
  select id into v_actor_id from actor
   where slug=v_actor_slug and kind='human' and active;
  if v_actor_id is null then
    raise exception 'authority actor % is not an active human',v_actor_slug;
  end if;
  if btrim(coalesce(p_idempotency_key,''))='' or btrim(coalesce(p_reason,''))='' then
    raise exception 'amendment idempotency key and rationale are required';
  end if;
  v_new_statement := btrim(coalesce(p_new_statement,''));
  if v_new_statement='' then
    raise exception 'a rule cannot be amended to empty text';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('rule-amendment:'||p_idempotency_key,0));
  select * into v_prior from ops.rule_amendment_receipt
   where idempotency_key=p_idempotency_key;
  if found then
    if v_prior.rule_id is distinct from p_rule_id
       or v_prior.new_statement is distinct from v_new_statement
       or v_prior.rationale is distinct from btrim(p_reason)
       or v_prior.amended_by is distinct from v_actor_id then
      raise exception 'rule amendment idempotency key was reused with different input';
    end if;
    select * into v_rule from rule where id=p_rule_id for update;
    if not found
       or v_rule.version is distinct from v_prior.rule_version_after
       or v_rule.statement is distinct from v_prior.new_statement then
      raise exception 'rule amendment replay refused: current rule no longer matches the immutable amendment';
    end if;
    return jsonb_build_object('ok',true,'replayed',true,'rule_id',p_rule_id,
      'rule_version_before',v_prior.rule_version_before,
      'rule_version_after',v_prior.rule_version_after,
      'amendment_receipt_id',v_prior.id);
  end if;

  select * into v_rule from rule where id=p_rule_id for update;
  if not found then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status='retired' then
    raise exception 'rule % is retired; a withdrawn rule stays as written',p_rule_id;
  end if;
  if v_rule.status not in ('proposed','active') then
    raise exception 'rule % is %, expected proposed or active',p_rule_id,v_rule.status;
  end if;

  v_prior_hash := encode(digest(v_rule.statement,'sha256'),'hex');
  v_new_hash   := encode(digest(v_new_statement,'sha256'),'hex');
  if v_prior_hash = v_new_hash then
    raise exception 'rule % amendment is a no-op: the new statement hashes identically to the current one',p_rule_id;
  end if;

  v_amended_at := now();
  v_contract := jsonb_build_object(
    'rule_id',v_rule.id,'rule_version_before',v_rule.version,'rule_version_after',v_rule.version+1,
    'prior_statement_hash',v_prior_hash,'new_statement_hash',v_new_hash,
    'actor_id',v_actor_id,'rationale',btrim(p_reason),'amended_at',v_amended_at);
  v_contract_hash := encode(digest(v_contract::text,'sha256'),'hex');

  insert into ops.rule_amendment_receipt
    (idempotency_key,rule_id,rule_version_before,rule_version_after,prior_statement_hash,
     new_statement,new_statement_hash,amended_by,rationale,contract_hash,amended_at)
  values (p_idempotency_key,v_rule.id,v_rule.version,v_rule.version+1,v_prior_hash,
          v_new_statement,v_new_hash,v_actor_id,btrim(p_reason),v_contract_hash,v_amended_at)
  returning * into v_receipt;

  insert into ops.authority_receipt
    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
  values ('amendment:'||p_idempotency_key,'amendment','rule',v_rule.id,v_actor_id,
          'statement amended by Joe authority: '||btrim(p_reason),v_contract_hash,'{}'::text[]);

  update rule set statement=v_new_statement where id=v_rule.id and version=v_rule.version;
  if not found then raise exception 'rule % amendment raced',v_rule.id; end if;

  return jsonb_build_object('ok',true,'replayed',false,'rule_id',v_rule.id,
    'rule_version_before',v_rule.version,'rule_version_after',v_rule.version+1,
    'amendment_receipt_id',v_receipt.id);
end $$;

comment on function ops.amend_rule_statement(uuid, text, text, text) is
    'Guarded like ops.approve_rule and ops.retire_rule: Joe-authority actor only, refuses a retired rule, writes an immutable ops.rule_amendment_receipt hashing the PRIOR statement, then updates rule.statement atomically. human_quote/scope/taught_by/personal_to/supersedes and every activation/retirement field are untouched. Wired by the amend-rule verb for the ACTIVE-rule case only; a still-PROPOSED rule keeps its existing direct-update amend-in-place path, which never required Joe authority.';

revoke all on function ops.amend_rule_statement(uuid, text, text, text) from public;
grant execute on function ops.amend_rule_statement(uuid, text, text, text) to carr_authority;

-- ── permit exactly this path through the immutability trigger ──────────────

create or replace function ops.require_rule_admission() returns trigger
    language plpgsql
    as $$
declare
  a ops.rule_admission%rowtype;
  v_approval ops.rule_approval_receipt%rowtype;
begin
  if tg_op='UPDATE' and old.status='retired' then
    raise exception 'retired rule % is immutable',old.id;
  end if;
  -- Once the approval receipt exists, the entire rule row is immutable except
  -- for the three exact authority transitions owned below: proposed -> active
  -- in ops.approve_rule, proposed/active -> retired in ops.retire_rule, and
  -- (new, 0349) active -> active with ONLY the statement changed, in
  -- ops.amend_rule_statement. This also blocks no-op UPDATEs that would
  -- otherwise bump the optimistic version and silently make an active receipt
  -- stale through trg_touch_row.
  if tg_op='UPDATE'
     and exists (select 1 from ops.rule_approval_receipt where rule_id=old.id) then
    if old.status='proposed' and new.status='active'
       and new.id is not distinct from old.id
       and new.statement is not distinct from old.statement
       and new.human_quote is not distinct from old.human_quote
       and new.taught_by is not distinct from old.taught_by
       and new.scope is not distinct from old.scope
       and new.personal_to is not distinct from old.personal_to
       and new.supersedes is not distinct from old.supersedes
       and new.created_at is not distinct from old.created_at
       and new.version is not distinct from old.version
       and new.updated_at is not distinct from old.updated_at
       and new.retired_by is not distinct from old.retired_by
       and new.retired_at is not distinct from old.retired_at then
      null; -- exact activation fields are validated below
    elsif old.status in ('proposed','active') and new.status='retired'
       and new.id is not distinct from old.id
       and new.statement is not distinct from old.statement
       and new.human_quote is not distinct from old.human_quote
       and new.taught_by is not distinct from old.taught_by
       and new.scope is not distinct from old.scope
       and new.personal_to is not distinct from old.personal_to
       and new.activated_by is not distinct from old.activated_by
       and new.activated_at is not distinct from old.activated_at
       and new.enforcement is not distinct from old.enforcement
       and new.supersedes is not distinct from old.supersedes
       and new.created_at is not distinct from old.created_at
       and new.version is not distinct from old.version
       and new.updated_at is not distinct from old.updated_at then
      null; -- exact retirement actor/receipt is validated below
    elsif old.status='active' and new.status='active'
       and new.id is not distinct from old.id
       and new.statement is distinct from old.statement
       and new.human_quote is not distinct from old.human_quote
       and new.taught_by is not distinct from old.taught_by
       and new.scope is not distinct from old.scope
       and new.personal_to is not distinct from old.personal_to
       and new.activated_by is not distinct from old.activated_by
       and new.activated_at is not distinct from old.activated_at
       and new.enforcement is not distinct from old.enforcement
       and new.supersedes is not distinct from old.supersedes
       and new.created_at is not distinct from old.created_at
       and new.version is not distinct from old.version
       and new.updated_at is not distinct from old.updated_at
       and new.retired_by is not distinct from old.retired_by
       and new.retired_at is not distinct from old.retired_at
       and exists (
         select 1 from ops.rule_amendment_receipt ar
          where ar.rule_id=old.id
            and ar.rule_version_before=old.version
            and ar.prior_statement_hash=encode(digest(old.statement,'sha256'),'hex')
            and ar.new_statement=new.statement
       ) then
      null; -- exact amendment receipt is validated below
    else
      raise exception 'approved rule % is immutable except through exact Joe approval, retirement or amendment',new.id;
    end if;
  end if;
  if tg_op='UPDATE' and old.status is distinct from 'retired' and new.status='retired' then
    if new.retired_by is null or new.retired_at is null or not exists (
      select 1 from ops.rule_retirement_receipt rr
       where rr.rule_id=old.id and rr.actor_id=new.retired_by
         and rr.rule_version_before=old.version
         and rr.statement_hash=encode(digest(old.statement,'sha256'),'hex')
         and rr.previous_status=old.status
    ) then
      raise exception 'rule % cannot retire without an exact Joe authority receipt',new.id;
    end if;
  end if;
  if not (new.status='active' and
          (tg_op='INSERT' or old.status is distinct from 'active')) then
    return new;
  end if;
  if new.activated_by is null then
    raise exception 'rule % cannot activate without a human activator',new.id;
  end if;
  select * into a from ops.rule_admission where rule_id=new.id;
  if not found or a.state<>'admitted' then
    raise exception 'rule % cannot activate: admitted rule contract is missing',new.id;
  end if;
  if a.enforcement_status not in ('hard_enforced','authority_enforced') then
    raise exception 'rule % cannot activate: active requires installed enforcement, got %',
      new.id,a.enforcement_status;
  end if;
  select * into v_approval from ops.rule_approval_receipt
   where rule_id=new.id and actor_id=new.activated_by
     and enforcement_status=a.enforcement_status
     and statement_hash=encode(digest(new.statement,'sha256'),'hex')
   order by created_at desc limit 1;
  if not found then
    raise exception 'rule % cannot activate: immutable enforced approval receipt is missing',new.id;
  end if;
  if new.enforcement is distinct from
       (case when v_approval.enforcement_status='hard_enforced' then 'gate' else 'constraint' end) then
    raise exception 'rule % cannot activate: enforcement label does not match approval',new.id;
  end if;
  if exists (
    select 1 from unnest(v_approval.requested_control_keys) as requested(control_key)
     where not exists (
       select 1
         from ops.rule_enforcement_point ep
         join ops.enforcement_control_catalog c using (control_key)
         join ops.rule_control_binding b
           on b.rule_id=ep.rule_id and b.control_key=ep.control_key
        where ep.rule_id=new.id and ep.control_key=requested.control_key
          and ep.installed and c.installed and c.verified_at is not null
          and b.statement_hash=encode(digest(new.statement,'sha256'),'hex')
          and c.enforcement_class in ('deny_gate','stop_gate','schema','transactional_schema')
     )
  ) then
    raise exception 'rule % cannot activate: exact requested enforcement is incomplete',new.id;
  end if;
  return new;
end $$;

drop trigger if exists rule_activation_requires_admission on rule;
create trigger rule_activation_requires_admission
    before insert or update on rule
    for each row execute function ops.require_rule_admission();

-- ── keep an amended ACTIVE rule reciting: widen the one match condition ────
--
-- Unchanged for every rule that has never been amended (the first disjunct is
-- the exact original expression). A rule reached through
-- ops.amend_rule_statement now also matches when ops.rule_amendment_reaches()
-- proves an unbroken hash-chained path from the approval receipt's frozen
-- text to the rule's current text.
create or replace function ops.applicable_rules(p_workflow text default null, p_surface text default null, p_tier text default null)
returns table(rule_id uuid, statement text, enforcement_class text, binding_moment text, applicability jsonb)
    language sql stable security definer
    set search_path to 'ops', 'public', 'pg_temp'
    as $$
  select r.id,r.statement,a.enforcement_class,a.binding_moment,a.applicability
    from rule r
    join ops.rule_admission a on a.rule_id=r.id
    join ops.rule_approval_receipt ar
      on ar.rule_id=r.id and ar.actor_id=r.activated_by
     and (
       (
         (ar.rule_version=r.version or exists (
           select 1 from ops.rule_approval_lifecycle_anchor legacy
            where legacy.approval_receipt_id=ar.id and legacy.rule_id=r.id
              and legacy.rule_version_after=r.version
              and legacy.statement_hash=ar.statement_hash))
         and ar.statement_hash=encode(digest(r.statement,'sha256'),'hex')
       )
       -- (0349) An amended active rule's VERSION and STATEMENT both moved
       -- together, so both the version-match and the hash-match above are
       -- expected to fail for it -- rule_amendment_reaches() proves the two
       -- moved together through a genuine, tamper-evident chain rather than
       -- checking either number in isolation.
       or ops.rule_amendment_reaches(r.id,ar.rule_version,ar.statement_hash,
                                      r.version,encode(digest(r.statement,'sha256'),'hex'))
     )
     and ar.policy_kind=a.enforcement_class
     and ar.enforcement_status=a.enforcement_status
     and ar.normalized_contract->>'binding_moment'=a.binding_moment
     and ar.normalized_contract->'applicability'=a.applicability
     and ar.normalized_contract->'projection'=a.projection
     and ar.normalized_contract->'reachability'=a.reachability
     and ar.normalized_contract->'input_contract'=a.input_contract
     and ar.evidence_refs=a.fixture_refs
   where r.status='active' and a.state='admitted' and a.admitted_by=ar.actor_id
     and exists (
       select 1 from ops.authority_receipt auth
        where auth.idempotency_key='approval:'||ar.idempotency_key
          and auth.kind='activation' and auth.subject_type='rule'
          and auth.subject_id=r.id and auth.actor_id=ar.actor_id
          and auth.contract_hash=ar.contract_hash)
     and not exists (
       select 1 from unnest(ar.requested_control_keys) requested(control_key)
        where not exists (
          select 1 from ops.rule_enforcement_point ep
          join ops.enforcement_control_catalog c using (control_key)
          join ops.rule_control_binding b
            on b.rule_id=ep.rule_id and b.control_key=ep.control_key
         where ep.rule_id=r.id and ep.control_key=requested.control_key
           and ep.installed and c.installed and c.verified_at is not null
           and b.statement_hash=ar.statement_hash
           and c.enforcement_class in ('deny_gate','stop_gate','schema','transactional_schema')))
     and (p_workflow is null or not (a.applicability ? 'workflows')
          or a.applicability->'workflows' ? '*'
          or a.applicability->'workflows' ? p_workflow)
     and (p_surface is null or not (a.applicability ? 'surfaces')
          or a.applicability->'surfaces' ? '*'
          or a.applicability->'surfaces' ? p_surface)
     and (p_tier is null or not (a.applicability ? 'tiers')
          or a.applicability->'tiers' ? '*'
          or a.applicability->'tiers' ? p_tier)
   order by r.created_at,r.id
$$;

comment on function ops.applicable_rules(text, text, text) is
    'Compiles the active admitted rule set for a workflow/surface/tier. SECURITY DEFINER with a pinned search_path (0188): the caller is the worker''s carr_reader role, which is views-only by design and cannot read public.rule or ops.rule_admission directly. Read-only, no dynamic SQL. Statement match accepts the original exact approval hash OR (0349) a proven ops.rule_amendment_receipt chain, so an amended active rule keeps reciting instead of silently dropping out.';

-- ── self-verification ────────────────────────────────────────────────────
do $$
begin
  if not exists (
    select 1 from information_schema.tables
     where table_schema='ops' and table_name='rule_amendment_receipt'
  ) then
    raise exception '0349 FAILED: ops.rule_amendment_receipt was not created';
  end if;

  if not exists (
    select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
     where n.nspname='ops' and p.proname='amend_rule_statement'
  ) then
    raise exception '0349 FAILED: ops.amend_rule_statement was not created';
  end if;

  if not exists (
    select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
     where n.nspname='ops' and p.proname='rule_amendment_reaches'
  ) then
    raise exception '0349 FAILED: ops.rule_amendment_reaches was not created';
  end if;

  if not exists (
    select 1 from pg_trigger t join pg_class c on c.oid=t.tgrelid
     where c.relname='rule_amendment_receipt' and t.tgname='rule_amendment_receipt_append_only'
  ) then
    raise exception '0349 FAILED: rule_amendment_receipt_append_only trigger is missing';
  end if;

  -- A neighbouring, unrelated function must be untouched in shape, or this
  -- migration reached further than it was meant to.
  if not exists (
    select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
     where n.nspname='ops' and p.proname='approve_rule'
  ) then
    raise exception '0349 FAILED: ops.approve_rule is gone — an unrelated live function was removed';
  end if;
end $$;

commit;
