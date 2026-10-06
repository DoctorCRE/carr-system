-- 0348_pr_only_main_ruleset_control.sql
--
-- WHY THIS EXISTS. WR-000019 slice S3 retired hooks/git-writer-gate.py and
-- hooks/canonical-edit-gate.py, and ops/config/rule-enforcement-map.json no
-- longer declares the git_writer or canonical_edit controls. The two rules
-- that named them (308ef1de, 4a53ff82) are reassigned in that same change to
-- a new control, pr_only_main_ruleset -- the PR-only main ruleset plus
-- required hosted CI that already carries the same property server-side
-- (a change cannot reach main without a reviewed PR), backed locally by
-- ops/githooks/pre-commit's existing refusal of a direct commit on main.
--
-- ops/control-catalog-parity-gate.py compares ops.enforcement_control_catalog
-- against exactly what ops/sync_control_catalog.py compiles from the map, so
-- a control the map declares that the table does not carry fails CI the
-- moment the map changes. This migration is the seed for the new row, and the
-- guarded retirement of the two rows the map no longer names -- the same
-- shape as migration 0290's retirement of ledger_boundary, applied twice.
--
-- WHY THE TWO OLD ROWS CANNOT SIMPLY BE LEFT BEHIND. An unreviewed row in this
-- table is an unreviewed claim about what counts as enforcement (rule
-- ab814a26), and the parity gate refuses to delete one itself for exactly
-- that reason -- it will not silently un-enforce a rule that might still be
-- bound to it.
--
-- A THIRD CONTROL NARROWS ITS EVIDENCE, THE SAME WAY 0290 NARROWED
-- record_home. loose_work's implementation also named
-- hooks/staging-attribution-gate.py, retired in the same slice; the map now
-- lists only hooks/loose-work-gate.py and hooks/staging-observation-tracker.py
-- for it. implementation_ref is compared string-for-string by
-- ops/control-catalog-parity-gate.py, so a map that no longer names the file
-- and a catalog row that still does is a DISAGREEMENT and fails the gate
-- exactly as an undeclared row does.
--
-- THE FOURTH THING THIS TOUCHES: ops.rule_delivery_activation_target.map_digest,
-- the stored preimage ops.set_rule_delivery_mode() compares the caller's
-- CARR_CHANGE_REASON-style expected digest against before it will flip
-- ops.rule_delivery_policy (migration 0317's cutover function; last refreshed
-- by migration 0332 to the map's PRE-slice-S3 sha256). Every edit this slice
-- made to ops/config/rule-enforcement-map.json changed its bytes, so the
-- digest ops/rule-delivery-local-pg-acceptance.py now reads from
-- ops/config/rule-delivery-activation-overlay.v1.json's freshly re-pinned
-- base_map_sha256 no longer matches the nine stored rows, and the cutover
-- function raises 'activation map digest preimage differs'. This migration
-- refreshes map_digest on those same nine rows the same way 0332 did --
-- nothing else on the row changes, because nothing this slice touched names
-- session_boot, pack_delivery, or any of the nine reviewed rule ids (verified
-- against the branch diff; see the overlay file's own _note).
--
-- WHY IT IS SAFE TO RETIRE THEM HERE, checked by the database rather than
-- taken on trust, same three facts 0290 checked:
--   1. rule_controls in ops/config/rule-enforcement-map.json names neither
--      git_writer nor canonical_edit any more, across all 219 active rules.
--   2. ops.rule_control_binding references control_key ON DELETE RESTRICT, so
--      a live binding would abort this migration instead of silently
--      un-enforcing a rule.
--   3. active_approved_control_immutable fires BEFORE DELETE and raises if
--      the key appears in any rule_approval_receipt for an active rule.
-- If either fact is wrong for either control, this migration fails loudly
-- (naming which one) rather than removing enforcement quietly.

begin;

-- ── seed the new control, insert-only (migration 0274's pattern) ───────────
insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values (
    'pr_only_main_ruleset',
    'external:GitHub branch ruleset 20824501 on jbookout/carr-system''s main branch — PR-only, direct pushes rejected server-side with required status checks; ops/githooks/pre-commit',
    'ops/main-commit-gate-selftest.py; external:a direct `git push origin main` from a local clone is rejected by GitHub before it reaches the branch',
    'deny_gate',
    true,
    now()
)
on conflict (control_key) do nothing;

-- ── retire git_writer, guarded exactly like 0290 retired ledger_boundary ───
do $$
declare
  bound   integer;
  claimed integer;
begin
  if not exists (select 1 from ops.enforcement_control_catalog
                  where control_key = 'git_writer') then
    raise notice '0348: git_writer is already absent from the catalog; nothing to retire';
    return;
  end if;

  select count(*) into bound
    from ops.rule_control_binding
   where control_key = 'git_writer';
  if bound > 0 then
    raise exception '0348 REFUSED: git_writer still has % rule binding(s); '
                    'it is enforcing something and must not be retired', bound;
  end if;

  select count(*) into claimed
    from ops.rule_approval_receipt ar
    join rule r on r.id = ar.rule_id and r.status = 'active'
   where 'git_writer' = any(ar.requested_control_keys);
  if claimed > 0 then
    raise exception '0348 REFUSED: git_writer backs % active approved rule(s)', claimed;
  end if;

  delete from ops.enforcement_control_catalog where control_key = 'git_writer';
end $$;

-- ── retire canonical_edit, the same way ─────────────────────────────────────
do $$
declare
  bound   integer;
  claimed integer;
begin
  if not exists (select 1 from ops.enforcement_control_catalog
                  where control_key = 'canonical_edit') then
    raise notice '0348: canonical_edit is already absent from the catalog; nothing to retire';
    return;
  end if;

  select count(*) into bound
    from ops.rule_control_binding
   where control_key = 'canonical_edit';
  if bound > 0 then
    raise exception '0348 REFUSED: canonical_edit still has % rule binding(s); '
                    'it is enforcing something and must not be retired', bound;
  end if;

  select count(*) into claimed
    from ops.rule_approval_receipt ar
    join rule r on r.id = ar.rule_id and r.status = 'active'
   where 'canonical_edit' = any(ar.requested_control_keys);
  if claimed > 0 then
    raise exception '0348 REFUSED: canonical_edit backs % active approved rule(s)', claimed;
  end if;

  delete from ops.enforcement_control_catalog where control_key = 'canonical_edit';
end $$;

-- ── narrow loose_work's evidence, the same way 0290 narrowed record_home ──
do $$
declare
  updated integer;
begin
  update ops.enforcement_control_catalog
     set implementation_ref = 'hooks/loose-work-gate.py; hooks/staging-observation-tracker.py',
         updated_at = now()
   where control_key = 'loose_work'
     and implementation_ref <> 'hooks/loose-work-gate.py; hooks/staging-observation-tracker.py';
  get diagnostics updated = row_count;
  if updated = 0 then
    raise notice '0348: loose_work already carries the narrowed implementation list';
  end if;
end $$;

-- ── refresh the activation-target preimage digest, the same way 0332 did ──
do $$
declare
  v_updated bigint;
begin
  if (select mode from ops.rule_delivery_policy where singleton)
       is distinct from 'shadow' then
    raise exception '0348 REFUSED: rule delivery activation preimage refresh requires shadow mode';
  end if;

  update ops.rule_delivery_activation_target
     set map_digest = '4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218'
   where short_id in (
     '25fcddee','3fa17fa0','72e06bdf','581cb3fe','113b3833',
     '57d13061','c66dc739','49533583','557838a5'
   )
   and map_digest <> '4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218';

  get diagnostics v_updated = row_count;
  if v_updated = 0 then
    raise notice '0348: rule_delivery_activation_target already carries the current map digest';
  end if;
end $$;

do $$
begin
  if not exists (select 1 from ops.enforcement_control_catalog
                  where control_key = 'pr_only_main_ruleset') then
    raise exception '0348 FAILED: pr_only_main_ruleset was not seeded';
  end if;

  if (select count(*) from ops.rule_delivery_activation_target
       where map_digest <> '4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218') > 0 then
    raise exception '0348 FAILED: rule_delivery_activation_target still carries a stale map_digest';
  end if;

  if (select count(*) from ops.rule_delivery_activation_target) <> 9 then
    raise exception '0348 FAILED: rule_delivery_activation_target no longer has exactly nine rows';
  end if;

  if exists (select 1 from ops.enforcement_control_catalog
              where control_key = 'git_writer') then
    raise exception '0348 FAILED: git_writer is still in the catalog after the delete';
  end if;

  if exists (select 1 from ops.enforcement_control_catalog
              where control_key = 'canonical_edit') then
    raise exception '0348 FAILED: canonical_edit is still in the catalog after the delete';
  end if;

  if exists (select 1 from ops.enforcement_control_catalog
              where control_key = 'loose_work'
                and implementation_ref like '%staging-attribution-gate%') then
    raise exception '0348 FAILED: loose_work still names the retired staging-attribution-gate.py';
  end if;

  if not exists (select 1 from ops.enforcement_control_catalog
                  where control_key = 'loose_work'
                    and implementation_ref like '%hooks/loose-work-gate.py%') then
    raise exception '0348 FAILED: loose_work no longer names hooks/loose-work-gate.py';
  end if;

  -- A neighbouring, unrelated control must be untouched, or this migration
  -- reached further than it was meant to.
  if not exists (select 1 from ops.enforcement_control_catalog
                  where control_key = 'gate_edit') then
    raise exception '0348 FAILED: gate_edit is gone — an unrelated live control was removed';
  end if;
end $$;

commit;
