-- WR-000111: the producer cost ledger -- the durable half of
-- mcp-server/src/hierarchical-cost-ledger.v5.js.
--
-- The module's own closing paragraph (openLedgerCell, :1483-1488) names the gap
-- this migration closes: "ONE cell in ONE process ... not durable
-- serialization. Two processes against a stored ledger need a row lock or a
-- serializable transaction to supply this same single admission point."
-- ops.lock_cost_ledger_cell below IS that row lock, and
-- ops.commit_cost_ledger_operation is the single admission point it guards.
--
-- THE COLUMN NAMES ARE THE MODULE'S. amount_units, vendor_reference,
-- authorization_ceiling_units, entry_id, kind, tree_version, conversion_id and
-- basis_digest are lifted from ENTRY_KEYS/NODE_KEYS/TREE_KEYS, not from the
-- Work Request's older prose. A renamed field is a wrong field, not an alias,
-- and MTR-TREE-ROUNDTRIP proves the mapping by deep-equalling a read-back
-- projection against the in-memory one rather than asserting it.
--
-- NO TRANSACTION CONTROL. tools/migrate.py refuses a 0339_-or-later migration
-- carrying top-level transaction control, and this file is the first member of
-- a reviewed ATOMIC_MIGRATION_GROUP with 0520, 0521 and 0522: the surface below
-- must not reach the DEFERRABLE ops.scac_policy_epoch_refresh() constraint
-- trigger on public.schema_migrations without the v30 seal inside the same
-- runner-owned transaction.

do $wr111_preflight$
begin
  if not exists (select 1 from public.schema_migrations
    where filename = '0518_program_controller_seams_scac_successor.sql'
      and sha256 = '1e4aa9acd713ea43393ea8d04a5ef80e0d2cf09b04bfed94b4fac9ba3755240c') then
    raise exception '0519 requires the exact 0518 program-controller SCAC v29 successor';
  end if;
  if to_regprocedure('ops.scac_mutation_catalog_v29_current()') is null then
    raise exception '0519 requires the v29 live catalog surface installed by 0518';
  end if;
end $wr111_preflight$;

-- -------------------------------------------------------------------------
-- Five relations. The tree and its nodes, the OPERATION log (where uniqueness
-- and refusals live), the ENTRY log, and the compare-and-swap cell.
-- -------------------------------------------------------------------------

create table ops.cost_scope_tree (
  tree_ref text primary key check (tree_ref ~ '^[a-z0-9][a-z0-9_.:/-]{0,127}$'),
  tree_id text not null,
  -- FROZEN module field (TREE_KEYS). Stored verbatim so the round-trip can
  -- prove the mapping instead of hand-writing it.
  tree_version text not null,
  created_at timestamptz not null default now()
);

create table ops.cost_scope_node (
  tree_ref text not null references ops.cost_scope_tree(tree_ref),
  node_id text not null check (node_id ~ '^[a-z0-9][a-z0-9_.:/-]{0,127}$'),
  parent_node_id text,
  scope_kind text not null check (scope_kind in ('portfolio','child','slice')),
  authorization_ceiling_units bigint not null check (authorization_ceiling_units >= 0),
  primary key (tree_ref, node_id),
  foreign key (tree_ref, parent_node_id) references ops.cost_scope_node(tree_ref, node_id),
  -- PARENT_KIND's root law: exactly the portfolio kind has no parent.
  check ((scope_kind = 'portfolio') = (parent_node_id is null))
);

-- UNIQUENESS LIVES HERE, not on the entry relation. A conversion emits TWO
-- entries under ONE operation_id, so unique(tree_ref, operation_id) on the
-- entry log forbade a legal state. A REFUSED operation writes zero entries and
-- still advances ledgerVersion -- which counts APPLIED OPERATIONS, not entries
-- -- so a refusal has to be storable somewhere, and this is that somewhere.
create table ops.cost_ledger_operation (
  tree_ref text not null references ops.cost_scope_tree(tree_ref),
  operation_id text not null check (operation_id ~ '^[a-z0-9][a-z0-9_.:/-]{0,127}$'),
  kind text not null check (kind in ('cancel_reservation','convert_liability_to_actual',
    'convert_reservation_to_actual','post_actual','record_estimate','record_late_liability','reserve')),
  argument_digest text not null check (argument_digest ~ '^sha256:[0-9a-f]{64}$'),
  outcome jsonb not null,
  refusal_reason_id text check (refusal_reason_id is null or refusal_reason_id in
    ('ancestor_overdrawn','ceiling_exceeded','duplicate_reservation_id','duplicate_vendor_charge',
     'liability_not_open','prepared_commit_mismatch','reservation_not_open','version_conflict')),
  produced_version integer not null check (produced_version >= 0),
  applied_at timestamptz not null default now(),
  correlation_id text,
  primary key (tree_ref, operation_id)
);

create table ops.cost_ledger_entry (
  tree_ref text not null,
  sequence integer not null check (sequence >= 0),
  entry_id text not null,
  -- FROZEN module name. NOT entry_kind.
  kind text not null check (kind in
    ('actual','estimate','late_liability','liability_release','reservation','reservation_release')),
  node_id text not null,
  amount_units bigint not null check (amount_units >= 0),
  -- NOT unique: one conversion emits two rows under one operation_id.
  operation_id text not null,
  occurred_at timestamptz not null,
  reservation_id text,
  liability_id text,
  conversion_id text,
  vendor_reference text check (vendor_reference ~ '^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}$'),
  basis_digest text check (basis_digest ~ '^sha256:[0-9a-f]{64}$'),
  primary key (tree_ref, sequence),
  unique (tree_ref, entry_id),
  foreign key (tree_ref, operation_id) references ops.cost_ledger_operation(tree_ref, operation_id),
  foreign key (tree_ref, node_id) references ops.cost_scope_node(tree_ref, node_id)
);

-- MUTABLE BY DESIGN: this is the compare-and-swap row. It gets the reference
-- monitor guards and deliberately NOT the immutability raiser.
create table ops.cost_ledger_cell (
  tree_ref text primary key references ops.cost_scope_tree(tree_ref),
  ledger_version integer not null check (ledger_version >= 0),
  state_digest text not null,
  updated_at timestamptz not null default now()
);

-- Append-only enforcement, the 0511:254-265 idiom.
create or replace function ops.cost_ledger_rows_immutable()
returns trigger language plpgsql as $$ begin
  raise exception 'producer cost ledger scope, operation and entry rows are immutable';
end $$;

create trigger cost_scope_tree_immutable before update or delete
on ops.cost_scope_tree for each row execute function ops.cost_ledger_rows_immutable();
create trigger cost_scope_tree_no_truncate before truncate
on ops.cost_scope_tree for each statement execute function ops.cost_ledger_rows_immutable();
create trigger cost_scope_node_immutable before update or delete
on ops.cost_scope_node for each row execute function ops.cost_ledger_rows_immutable();
create trigger cost_scope_node_no_truncate before truncate
on ops.cost_scope_node for each statement execute function ops.cost_ledger_rows_immutable();
create trigger cost_ledger_operation_immutable before update or delete
on ops.cost_ledger_operation for each row execute function ops.cost_ledger_rows_immutable();
create trigger cost_ledger_operation_no_truncate before truncate
on ops.cost_ledger_operation for each statement execute function ops.cost_ledger_rows_immutable();
create trigger cost_ledger_entry_immutable before update or delete
on ops.cost_ledger_entry for each row execute function ops.cost_ledger_rows_immutable();
create trigger cost_ledger_entry_no_truncate before truncate
on ops.cost_ledger_entry for each statement execute function ops.cost_ledger_rows_immutable();
create trigger cost_ledger_cell_no_truncate before truncate
on ops.cost_ledger_cell for each statement execute function ops.cost_ledger_rows_immutable();

-- SIEP-18 reference-monitor guards on all five relations.
create trigger scac_reference_monitor_guard_row before insert or update or delete
on ops.cost_scope_tree for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on ops.cost_scope_tree for each statement execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_row before insert or update or delete
on ops.cost_scope_node for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on ops.cost_scope_node for each statement execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_row before insert or update or delete
on ops.cost_ledger_operation for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on ops.cost_ledger_operation for each statement execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_row before insert or update or delete
on ops.cost_ledger_entry for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on ops.cost_ledger_entry for each statement execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_row before insert or update or delete
on ops.cost_ledger_cell for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on ops.cost_ledger_cell for each statement execute function ops.scac_reference_monitor_guard();

-- -------------------------------------------------------------------------
-- Three definer functions. Every direct write is revoked below, so these are
-- the only doors: one to create a tree, one to take the cross-process lock,
-- one to move the cell.
-- -------------------------------------------------------------------------

create or replace function ops.initialize_cost_scope_tree(
  p_tree_ref text, p_compiled_tree jsonb, p_empty_state_digest text)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_node jsonb;
begin
  if exists (select 1 from ops.cost_scope_tree where tree_ref = p_tree_ref) then
    return jsonb_build_object('ok', false, 'reason_id', 'cost_scope_tree_exists',
      'tree_ref', p_tree_ref);
  end if;
  insert into ops.cost_scope_tree(tree_ref, tree_id, tree_version)
  values (p_tree_ref, p_compiled_tree->>'tree_id', p_compiled_tree->>'tree_version');
  -- Parents before children: the self-referencing foreign key is checked per
  -- row, so the node rows are ordered by their ancestor depth here rather than
  -- by whatever key order the caller's JSON happened to carry.
  for v_node in
    select value from jsonb_array_elements(p_compiled_tree->'node_rows')
     order by (value->>'depth')::integer, value->>'node_id'
  loop
    insert into ops.cost_scope_node(
      tree_ref, node_id, parent_node_id, scope_kind, authorization_ceiling_units)
    values (p_tree_ref, v_node->>'node_id', v_node->>'parent_node_id',
            v_node->>'scope_kind', (v_node->>'authorization_ceiling_units')::bigint);
  end loop;
  insert into ops.cost_ledger_cell(tree_ref, ledger_version, state_digest)
  values (p_tree_ref, 0, p_empty_state_digest);
  return jsonb_build_object('ok', true, 'tree_ref', p_tree_ref,
    'ledger_version', 0, 'state_digest', p_empty_state_digest);
end $$;

comment on function ops.initialize_cost_scope_tree(text,jsonb,text) is
  'WR-000111: the only way a cost scope tree, its nodes and its compare-and-swap cell come into existence. Every direct write on those relations is revoked.';

-- THE CROSS-PROCESS ADMISSION POINT. select ... for update and nothing else:
-- the lock is held for the CALLER's whole transaction, which is exactly what
-- hierarchical-cost-ledger.v5.js says a durable store must supply and what no
-- in-process cell can.
create or replace function ops.lock_cost_ledger_cell(p_tree_ref text)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_version integer; v_digest text;
begin
  select ledger_version, state_digest into v_version, v_digest
    from ops.cost_ledger_cell where tree_ref = p_tree_ref for update;
  if v_version is null then
    return jsonb_build_object('ok', false, 'reason_id', 'cost_ledger_cell_absent',
      'tree_ref', p_tree_ref);
  end if;
  return jsonb_build_object('ok', true, 'tree_ref', p_tree_ref,
    'ledger_version', v_version, 'state_digest', v_digest);
end $$;

comment on function ops.lock_cost_ledger_cell(text) is
  'WR-000111: takes the row lock the in-process ledger cell cannot. Held for the caller transaction; this is the serialization point the module names.';

create or replace function ops.commit_cost_ledger_operation(
  p_tree_ref text, p_base_version integer, p_base_state_digest text,
  p_operation_id text, p_argument_digest text, p_kind text,
  p_outcome jsonb, p_refusal_reason_id text,
  p_next_version integer, p_next_state_digest text, p_entries jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_prior ops.cost_ledger_operation%rowtype; v_cur integer; v_dig text; v_entry jsonb;
begin
  -- 1. REPLAY FIRST, before the cell is looked at at all. A legitimate replay
  --    offered from its ORIGINAL base must not meet the already-moved cell and
  --    come back as a conflict.
  select * into v_prior from ops.cost_ledger_operation
    where tree_ref = p_tree_ref and operation_id = p_operation_id;
  if v_prior.operation_id is not null then
    if v_prior.argument_digest is distinct from p_argument_digest then
      raise exception 'operation_id_reused_with_different_arguments'
        using errcode = '22023',
        detail = format('operation %s on tree %s was applied with a different argument digest',
          p_operation_id, p_tree_ref);
    end if;
    select ledger_version into v_cur from ops.cost_ledger_cell where tree_ref = p_tree_ref;
    return jsonb_build_object('ok', true, 'replayed', true,
      'outcome', v_prior.outcome, 'ledger_version', v_cur);
  end if;

  -- 2. THEN THE LOCK, inside this function. The library already holds it from
  --    step (0) of its own transaction and a second `for update` on a row this
  --    transaction already holds is a no-op -- so the library path is
  --    unchanged. What it adds is that an AUTHORITY caller reaching this
  --    function directly is serialized too.
  perform 1 from ops.cost_ledger_cell where tree_ref = p_tree_ref for update;

  -- 3. THEN THE BASE, as a predicated write. ZERO ROWS MATCHED IS THE REFUSAL.
  --    The predicate carries both the version and the digest, so the refusal is
  --    correct even for two operations that would land on the same number.
  update ops.cost_ledger_cell
     set ledger_version = p_next_version, state_digest = p_next_state_digest, updated_at = now()
   where tree_ref = p_tree_ref
     and ledger_version = p_base_version
     and state_digest = p_base_state_digest;
  if not found then
    select ledger_version, state_digest into v_cur, v_dig
      from ops.cost_ledger_cell where tree_ref = p_tree_ref;
    -- THE FOUR NAMES ARE THE MODULE'S OWN (conflictOutcome :1439-1447 and the
    -- version_conflict branch :1517-1527). Never expected_/actual_.
    return jsonb_build_object(
      'ok', false, 'reason_id', 'version_conflict',
      'base_version', p_base_version, 'current_version', v_cur,
      'base_state_digest', p_base_state_digest, 'current_state_digest', v_dig);
  end if;

  -- 4. THEN THE ROWS. A refusal is an applied operation with an empty entry
  --    array, and it is stored exactly like an accepted one.
  insert into ops.cost_ledger_operation(
    tree_ref, operation_id, kind, argument_digest, outcome, refusal_reason_id,
    produced_version, correlation_id)
  values (p_tree_ref, p_operation_id, p_kind, p_argument_digest, p_outcome,
          p_refusal_reason_id, p_next_version,
          nullif(current_setting('carr.correlation_id', true), ''));

  for v_entry in select value from jsonb_array_elements(coalesce(p_entries, '[]'::jsonb))
                  order by (value->>'sequence')::integer
  loop
    insert into ops.cost_ledger_entry(
      tree_ref, sequence, entry_id, kind, node_id, amount_units, operation_id,
      occurred_at, reservation_id, liability_id, conversion_id, vendor_reference, basis_digest)
    values (p_tree_ref, (v_entry->>'sequence')::integer, v_entry->>'entry_id',
            v_entry->>'kind', v_entry->>'node_id', (v_entry->>'amount_units')::bigint,
            v_entry->>'operation_id', (v_entry->>'occurred_at')::timestamptz,
            v_entry->>'reservation_id', v_entry->>'liability_id', v_entry->>'conversion_id',
            v_entry->>'vendor_reference', v_entry->>'basis_digest');
  end loop;

  return jsonb_build_object('ok', true, 'replayed', false, 'outcome', p_outcome,
    'ledger_version', p_next_version, 'state_digest', p_next_state_digest);
end $$;

comment on function ops.commit_cost_ledger_operation(text,integer,text,text,text,text,jsonb,text,integer,text,jsonb) is
  'WR-000111: the single admission point. Replay first, then the row lock, then a version-and-digest predicated cell move whose zero-row result IS the refusal, then the operation and entry rows.';

-- -------------------------------------------------------------------------
-- Grants. TWO precedents for two different things: the column-list shape comes
-- from migrations/0021_jobs_role_and_cadence_inputs.sql:97-105, and the
-- revoke-everything-then-narrow-execute bundle from
-- migrations/0511_foundation_assurance_minimum_outcome.sql:526-546.
--
-- argument_digest and correlation_id are EXCLUDED from carr_reader's column
-- grants -- that exclusion is what makes the grant column-scoped rather than
-- decorative.
-- -------------------------------------------------------------------------

grant select (tree_ref,tree_id,tree_version) on ops.cost_scope_tree to carr_reader;
grant select (tree_ref,node_id,parent_node_id,scope_kind,authorization_ceiling_units) on ops.cost_scope_node to carr_reader;
grant select (tree_ref,operation_id,kind,outcome,refusal_reason_id,produced_version,applied_at) on ops.cost_ledger_operation to carr_reader;
grant select (tree_ref,sequence,entry_id,kind,node_id,amount_units,operation_id,occurred_at,reservation_id,liability_id,conversion_id,vendor_reference,basis_digest) on ops.cost_ledger_entry to carr_reader;
grant select (tree_ref,ledger_version,state_digest,updated_at) on ops.cost_ledger_cell to carr_reader;

grant select on ops.cost_scope_tree,ops.cost_scope_node,ops.cost_ledger_operation,
                ops.cost_ledger_entry,ops.cost_ledger_cell to carr_writer,carr_authority;

revoke insert,update,delete,truncate on ops.cost_scope_tree,ops.cost_scope_node,
  ops.cost_ledger_operation,ops.cost_ledger_entry,ops.cost_ledger_cell
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

-- The revoke PRECEDES the grant so acldefault()'s public EXECUTE row is
-- suppressed and the secdef projection sees exactly the named grantees.
revoke all on function ops.initialize_cost_scope_tree(text,jsonb,text),
  ops.lock_cost_ledger_cell(text),
  ops.commit_cost_ledger_operation(text,integer,text,text,text,text,jsonb,text,integer,text,jsonb)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.initialize_cost_scope_tree(text,jsonb,text),
  ops.lock_cost_ledger_cell(text),
  ops.commit_cost_ledger_operation(text,integer,text,text,text,text,jsonb,text,integer,text,jsonb)
  to carr_authority;
